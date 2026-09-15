"""Tests for the pure framing policy and its geometry primitives."""

from core.geometry import (
    Rect,
    clamp_to_source,
    expand,
    fit_ratio,
    to_crop,
    union,
)
from core.policy import PolicyParams, PolicyState, initial_state, step


def _target(boxes: list[Rect], p: PolicyParams) -> Rect:
    """Run the same geometry pipeline the policy uses for a target."""
    return clamp_to_source(
        fit_ratio(expand(union(boxes), p.margin), p.ratio),
        p.source_w,
        p.source_h,
        p.ratio,
        p.min_crop_h,
    )


# --- geometry -----------------------------------------------------------


def test_union_of_empty_list_is_none():
    assert union([]) is None


def test_fit_ratio_matches_target_ratio():
    r = fit_ratio(Rect(100, 100, 300, 200), 0.5625)
    assert abs(r.w / r.h - 0.5625) < 1e-9


def test_clamp_drops_below_min_h_when_the_source_is_too_narrow():
    r = clamp_to_source(Rect(100, 100, 50, 50), 400, 1080, 0.5625, 960)
    assert abs(r.w - 400) < 1e-9
    assert r.h < 960
    assert abs(r.w / r.h - 0.5625) < 1e-9
    assert r.x >= 0 and r.right <= 400 + 1e-9
    assert r.y >= 0 and r.bottom <= 1080 + 1e-9


def test_clamp_to_source_keeps_body_at_top_left_edge_inside_source():
    p = PolicyParams()
    r = clamp_to_source(Rect(-50, -50, 200, 200), p.source_w, p.source_h, p.ratio, p.min_crop_h)
    assert r.x >= 0 and r.y >= 0
    assert r.right <= p.source_w and r.bottom <= p.source_h
    assert abs(r.w / r.h - p.ratio) < 1e-9


def test_clamp_to_source_keeps_body_at_bottom_right_edge_inside_source():
    p = PolicyParams()
    r = clamp_to_source(Rect(1900, 1000, 50, 50), p.source_w, p.source_h, p.ratio, p.min_crop_h)
    assert r.x >= 0 and r.y >= 0
    assert r.right <= p.source_w and r.bottom <= p.source_h
    assert abs(r.w / r.h - p.ratio) < 1e-9


def test_clamp_to_source_caps_an_oversized_min_h_at_source_h():
    p = PolicyParams()
    r = clamp_to_source(Rect(800, 400, 300, 300), p.source_w, p.source_h, p.ratio, min_h=2000.0)
    assert r.h == p.source_h
    assert r.right <= p.source_w and r.bottom <= p.source_h
    assert abs(r.w / r.h - p.ratio) < 1e-9


def test_to_crop_is_never_negative_and_never_degenerate():
    source_w, source_h = 1920, 1080
    left, top, right, bottom = to_crop(Rect(0, 0, 540, 960), source_w, source_h)
    assert min(left, top, right, bottom) >= 0
    assert left + right < source_w
    assert top + bottom < source_h


def test_to_crop_stays_valid_for_a_rect_positioned_outside_the_source():
    source_w, source_h = 1920, 1080
    left, top, right, bottom = to_crop(Rect(2000, -200, 10, 10), source_w, source_h)
    assert min(left, top, right, bottom) >= 0
    assert left + right < source_w
    assert top + bottom < source_h


# --- policy ---------------------------------------------------------------


def test_jitter_in_place_never_reframes():
    p = PolicyParams()
    steady = _target([Rect(800, 200, 300, 700)], p)
    state = PolicyState(current=steady, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)

    jitters = [(3, -3, 3, -3), (-3, 3, -3, 3), (5, 5, -5, -5), (-5, -5, 5, 5), (0, 2, -2, 0), (2, 0, 0, -2)]
    now_ms = 0.0
    for i in range(40):
        dx, dy, dw, dh = jitters[i % len(jitters)]
        box = Rect(800 + dx, 200 + dy, 300 + dw, 700 + dh)
        now_ms += 150.0
        state, command = step(state, [box], now_ms, p)
        assert command is None
        assert state.current == steady


def test_drift_out_and_back_resets_the_dwell():
    p = PolicyParams()
    steady = _target([Rect(800, 200, 300, 700)], p)
    moved_box = Rect(1200, 200, 300, 700)
    state = PolicyState(current=steady, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)

    state, cmd = step(state, [moved_box], 100.0, p)  # first departure
    assert cmd is None and state.pending_since_ms == 100.0

    state, cmd = step(state, [Rect(800, 200, 300, 700)], 250.0, p)  # back inside dwell_ms
    assert cmd is None and state.pending is None

    state, cmd = step(state, [moved_box], 300.0, p)  # second departure
    assert cmd is None and state.pending_since_ms == 300.0

    # 500 - 100 == dwell_ms: would already have committed if timed from
    # the first departure. It must not have.
    state, cmd = step(state, [moved_box], 500.0, p)
    assert cmd is None

    state, cmd = step(state, [moved_box], 699.0, p)
    assert cmd is None

    state, cmd = step(state, [moved_box], 700.0, p)  # 300 + dwell_ms
    assert cmd is not None
    assert cmd.reason == "commit"


def test_sustained_move_commits_after_dwell():
    p = PolicyParams()
    steady = _target([Rect(800, 200, 300, 700)], p)
    moved_box = Rect(1200, 200, 300, 700)
    state = PolicyState(current=steady, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)

    state, cmd = step(state, [moved_box], 0.0, p)
    assert cmd is None

    state, cmd = step(state, [moved_box], p.dwell_ms - 1, p)
    assert cmd is None  # not one step before dwell_ms

    state, cmd = step(state, [moved_box], p.dwell_ms, p)
    assert cmd is not None
    assert cmd.reason == "commit"
    assert cmd.target == _target([moved_box], p)


def test_no_transition_stacking():
    p = PolicyParams()
    steady = _target([Rect(800, 200, 300, 700)], p)
    moved_box = Rect(1200, 200, 300, 700)
    state = PolicyState(current=steady, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)
    state, _ = step(state, [moved_box], 0.0, p)  # start the dwell countdown
    state, cmd = step(state, [moved_box], p.dwell_ms, p)
    assert cmd is not None and state.busy_until_ms is not None

    busy_state = state
    state, cmd = step(state, [Rect(800, 200, 300, 700)], state.busy_until_ms - 1, p)
    assert cmd is None
    # Transition in flight: the applied crop and its own timers don't move.
    # tracks does update through the lock (issue #2, fix c), so it's excluded.
    assert state.current == busy_state.current
    assert state.pending == busy_state.pending
    assert state.busy_until_ms == busy_state.busy_until_ms
    assert state.mode == busy_state.mode


def test_lost_subject_holds_then_widens():
    p = PolicyParams()
    steady = _target([Rect(800, 200, 300, 700)], p)
    state = PolicyState(current=steady, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)

    state, cmd = step(state, [], p.hold_ms - 1, p)
    assert cmd is None and state.current == steady

    state, cmd = step(state, [], p.hold_ms, p)  # hold expires, dwell starts
    assert cmd is None and state.pending is not None

    state, cmd = step(state, [], p.hold_ms + p.dwell_ms, p)
    assert cmd is not None
    assert cmd.reason == "lost"


def test_two_bodies_are_framed_together():
    p = PolicyParams()
    box_a = Rect(700, 300, 150, 600)
    box_b = Rect(950, 300, 150, 600)
    far = Rect(0.0, 0.0, 540.0, 960.0)
    state = PolicyState(current=far, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)

    state, cmd = step(state, [box_a, box_b], 0.0, p)
    assert cmd is None
    state, cmd = step(state, [box_a, box_b], p.dwell_ms, p)

    assert cmd is not None
    t = cmd.target
    assert t.x <= box_a.x and t.y <= box_a.y and t.right >= box_a.right and t.bottom >= box_a.bottom
    assert t.x <= box_b.x and t.y <= box_b.y and t.right >= box_b.right and t.bottom >= box_b.bottom


def test_initial_state_is_full_height_centered_default():
    p = PolicyParams()
    state = initial_state(p)
    assert state.current.h == p.source_h
    assert state.current.cx == p.source_w / 2
    assert state.pending is None and state.busy_until_ms is None


# --- split mode -------------------------------------------------------------


def test_two_far_subjects_enter_split():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    replaced = _target([box_a, box_b], p)
    assert replaced.h == p.source_h  # the degenerate target split is meant to replace

    state = initial_state(p)
    state, cmd = step(state, [box_a, box_b], 0.0, p)
    assert cmd is None and state.mode == "single"

    state, cmd = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert cmd is not None
    assert state.mode == "split"
    assert cmd.mode == "split" and cmd.cells is not None


def test_two_close_subjects_stay_single():
    p = PolicyParams()
    close_a = Rect(700, 300, 150, 600)
    close_b = Rect(950, 300, 150, 600)
    far = Rect(0.0, 0.0, 540.0, 960.0)
    state = PolicyState(current=far, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)

    state, cmd = step(state, [close_a, close_b], 0.0, p)
    state, cmd = step(state, [close_a, close_b], p.dwell_ms, p)

    assert state.mode == "single"
    assert cmd is not None and cmd.mode == "single" and cmd.cells is None


def test_split_survives_losing_a_subject():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)

    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, cmd = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert cmd is not None and state.mode == "split"

    t = p.split_enter_ms
    while t < p.split_enter_ms + p.track_hold_ms - 200.0:  # strictly below track_hold_ms
        t += 200.0
        state, _ = step(state, [box_a], t, p)
        assert state.mode == "split"

    held = next(tr for tr in state.tracks if tr is not None and tr.box == box_b)
    assert held.last_seen_ms == p.split_enter_ms  # frozen: never re-detected during the dropout

    # Both subjects are back exactly where they were: nothing moved, so the
    # dead zone holds and no new command fires -- that discipline is the point.
    state, cmd = step(state, [box_a, box_b], t + 200.0, p)
    assert state.mode == "split" and cmd is None


def test_split_does_not_flap():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    close_a = Rect(700, 300, 150, 600)
    close_b = Rect(950, 300, 150, 600)

    # Entering: alternate far/close every 300ms, always short of split_enter_ms.
    state = initial_state(p)
    t = 0.0
    for i in range(10):
        boxes = [box_a, box_b] if i % 2 == 0 else [close_a, close_b]
        t += 300.0
        state, _ = step(state, boxes, t, p)
    assert state.mode == "single"

    # Once split, oscillate the same way: bursts always short of split_exit_ms.
    state, _ = step(state, [box_a, box_b], t, p)
    state, cmd = step(state, [box_a, box_b], t + p.split_enter_ms, p)
    assert cmd is not None and state.mode == "split"
    t += p.split_enter_ms
    for i in range(10):
        boxes = [close_a, close_b] if i % 2 == 0 else [box_a, box_b]
        t += 500.0
        state, _ = step(state, boxes, t, p)
        assert state.mode == "split"


def test_cells_never_swap():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, cmd = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert state.mode == "split"

    # Small jitter stays inside the dead zone, so most frames commit nothing;
    # state.cells (the last applied cells) is what must never swap, whether
    # or not this particular frame committed a change.
    jitters = [(3, -3), (-3, 3), (5, 5), (-5, -5), (0, 2), (2, 0)]
    t = p.split_enter_ms
    for i in range(30):
        dx, dy = jitters[i % len(jitters)]
        t += 150.0
        jittered_a = Rect(box_a.x + dx, box_a.y + dy, box_a.w, box_a.h)
        jittered_b = Rect(box_b.x + dx, box_b.y - dy, box_b.w, box_b.h)
        state, cmd = step(state, [jittered_a, jittered_b], t, p)
        assert state.mode == "split"
        top, bottom = state.cells
        assert top.cx < bottom.cx  # box_a keeps the smaller centre x, must stay on top
        if cmd is not None:
            assert cmd.cells[0].cx < cmd.cells[1].cx


def test_single_subject_never_splits():
    p = PolicyParams()
    box = Rect(800, 200, 300, 700)
    state = initial_state(p)
    t = 0.0
    for _ in range(20):
        t += 300.0
        state, _ = step(state, [box], t, p)
    assert state.mode == "single"


def test_split_cell_rects_keep_cell_ratio_and_stay_in_source():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, cmd = step(state, [box_a, box_b], p.split_enter_ms, p)

    for cell in cmd.cells:
        assert abs(cell.w / cell.h - p.ratio * 2) < 1e-9
        assert cell.x >= 0 and cell.right <= p.source_w
        assert cell.y >= 0 and cell.bottom <= p.source_h


def test_split_static_subjects_emit_nothing():
    p = PolicyParams()
    box_g = Rect(270, 165, 480, 825)
    box_d = Rect(1290, 90, 450, 900)
    state = initial_state(p)

    n_commands = 0
    for i in range(120):  # 10s at 12fps, subjects perfectly static throughout
        state, cmd = step(state, [box_g, box_d], i * 83.3, p)
        if cmd is not None:
            n_commands += 1
    assert state.mode == "split"
    assert n_commands == 1  # only the entry commit; no per-frame re-commits


def test_split_transitions_are_eased():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, _ = step(state, [box_a, box_b], p.split_enter_ms, p)

    # Shifted well clear of the source edge, so the cell itself moves rather
    # than staying pinned by clamp_to_source.
    moved_a = Rect(600, 200, 150, 700)
    t = p.split_enter_ms
    state, cmd = step(state, [moved_a, box_b], t, p)
    assert cmd is None and state.pending_cells is not None

    state, cmd = step(state, [moved_a, box_b], t + p.dwell_ms, p)
    assert cmd is not None
    assert cmd.duration_ms == p.ease_ms
    assert cmd.frm_cells is not None


# --- eye-line anchor and zoom hysteresis -----------------------------------


def test_eye_line_puts_the_anchor_a_third_down():
    # Small floor/max_zoom so the anchor's desired position is not clamped.
    p = PolicyParams(min_crop_h=1.0, max_zoom=100.0)
    box = Rect(800, 300, 300, 700, anchor=(950.0, 500.0))
    far = Rect(0.0, 0.0, 540.0, 960.0)  # far enough to guarantee a move regardless of height
    state = PolicyState(current=far, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)
    state, cmd = step(state, [box], 0.0, p)
    assert cmd is None

    state, cmd = step(state, [box], p.dwell_ms, p)
    assert cmd is not None
    target = cmd.target
    assert target.anchor is not None
    assert abs((target.anchor[1] - target.y) - p.eye_line * target.h) < 1.0


def test_anchor_clamped_at_the_top_edge_stays_in_source():
    p = PolicyParams()
    box = Rect(800, 0, 300, 700, anchor=(950.0, 20.0))
    far = Rect(0.0, 0.0, 540.0, 960.0)
    state = PolicyState(current=far, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)
    state, cmd = step(state, [box], 0.0, p)
    assert cmd is None

    state, cmd = step(state, [box], p.dwell_ms, p)
    assert cmd is not None
    target = cmd.target
    assert target.y >= 0 and target.bottom <= p.source_h + 1e-6
    assert (target.anchor[1] - target.y) / target.h < p.eye_line


def test_no_anchor_falls_back_to_centring():
    p = PolicyParams()
    box = Rect(800, 300, 300, 700)
    expected = _target([box], p)
    far = Rect(0.0, 0.0, 540.0, 960.0)
    state = PolicyState(current=far, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)
    state, cmd = step(state, [box], 0.0, p)
    state, cmd = step(state, [box], p.dwell_ms, p)
    assert cmd is not None
    assert cmd.target == expected


def test_height_noise_below_zoom_dead_zone_emits_nothing():
    # Wide headroom so the +-15% height jitter never hits clamp_to_source's
    # own floor or ceiling, which would otherwise mask the effect measured.
    p = PolicyParams(min_crop_h=1.0, max_zoom=100.0)
    steady = _target([Rect(900, 200, 150, 500)], p)
    state = PolicyState(current=steady, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)

    now_ms = 0.0
    for i in range(20):
        dh = 0.15 * 500 if i % 2 == 0 else -0.15 * 500
        box = Rect(900, 200, 150, 500 + dh)
        now_ms += 150.0
        state, cmd = step(state, [box], now_ms, p)
        assert cmd is None
    assert state.current == steady

    # The same relative jitter applied to the centre instead commits: it
    # crosses dead_zone even though it would stay below zoom_dead_zone.
    centre_state = PolicyState(current=steady, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)
    moved = Rect(1000, 200, 150, 500)
    centre_state, cmd = step(centre_state, [moved], 0.0, p)
    assert cmd is None
    centre_state, cmd = step(centre_state, [moved], p.dwell_ms, p)
    assert cmd is not None


def test_max_zoom_caps_the_crop_height():
    p = PolicyParams(min_crop_h=1.0)  # isolate max_zoom's floor from min_crop_h's
    box = Rect(860, 300, 200, 350)
    state = initial_state(p)
    state, cmd = step(state, [box], 0.0, p)
    assert cmd is None

    state, cmd = step(state, [box], p.dwell_ms, p)
    assert cmd is not None
    assert abs(cmd.target.h - p.source_h / p.max_zoom) < 1e-9


# --- bust cells and the crown invariant -------------------------------------


def test_split_cell_uses_the_bust_when_available():
    p = PolicyParams()
    box_b = Rect(1400, 200, 150, 700)

    box_a_full = Rect(300, 200, 150, 700)
    state = initial_state(p)
    state, _ = step(state, [box_a_full, box_b], 0.0, p)
    state, cmd_full = step(state, [box_a_full, box_b], p.split_enter_ms, p)

    # A tight head-and-shoulders sub-rect, well inside box_a_full.
    box_a_bust = Rect(300, 200, 150, 700, bust=(300.0, 200.0, 150.0, 180.0))
    state = initial_state(p)
    state, _ = step(state, [box_a_bust, box_b], 0.0, p)
    state, cmd_bust = step(state, [box_a_bust, box_b], p.split_enter_ms, p)

    assert cmd_bust.cells[0].h < cmd_full.cells[0].h * 0.75


def test_split_cell_falls_back_to_full_box_without_bust():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)  # bust=None
    box_b = Rect(1400, 200, 150, 700)
    cell_ratio = p.ratio * 2
    expected = clamp_to_source(
        fit_ratio(expand(box_a, p.margin), cell_ratio), p.source_w, p.source_h, cell_ratio, p.min_crop_h / 2, p.eye_line
    )

    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, cmd = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert cmd.cells[0] == expected


def test_split_cells_differ_when_heads_are_at_different_heights():
    p = PolicyParams()
    standing = Rect(300, 100, 200, 900)
    seated = Rect(1400, 400, 200, 500)
    state = initial_state(p)
    state, _ = step(state, [standing, seated], 0.0, p)
    state, cmd = step(state, [standing, seated], p.split_enter_ms, p)

    top_cell, bottom_cell = cmd.cells  # top: smaller cx, i.e. `standing`
    assert top_cell.h != bottom_cell.h
    assert top_cell.y != bottom_cell.y

    # Moving only the seated subject must leave the standing cell untouched:
    # cells are fit independently, not derived from one shared rect.
    moved_seated = Rect(1400, 100, 200, 500)
    state2 = initial_state(p)
    state2, _ = step(state2, [standing, moved_seated], 0.0, p)
    state2, cmd2 = step(state2, [standing, moved_seated], p.split_enter_ms, p)
    assert cmd2.cells[0] == top_cell
    assert cmd2.cells[1] != bottom_cell


def test_crop_never_cuts_the_head():
    p = PolicyParams()
    far = Rect(0.0, 0.0, 540.0, 960.0)

    # Below where centring alone would place the top edge, so the
    # invariant -- not a coincidence of the numbers -- has to hold it up.
    box = Rect(800, 300, 300, 700, crown=100.0)
    state = PolicyState(current=far, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)
    state, cmd = step(state, [box], 0.0, p)
    assert cmd is None
    state, cmd = step(state, [box], p.dwell_ms, p)
    assert cmd is not None
    assert cmd.target.y <= box.crown + 1e-9

    box_a = Rect(300, 200, 150, 700, crown=50.0)
    box_b = Rect(1400, 200, 150, 700, crown=60.0)
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, cmd = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert cmd.cells[0].y <= box_a.crown + 1e-9
    assert cmd.cells[1].y <= box_b.crown + 1e-9


def test_head_at_the_very_top_of_source_is_not_a_failure():
    p = PolicyParams()
    box = Rect(800, 0, 300, 700, crown=-50.0)  # crown above the source's own edge
    far = Rect(0.0, 0.0, 540.0, 960.0)
    state = PolicyState(current=far, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)
    state, cmd = step(state, [box], 0.0, p)
    assert cmd is None
    state, cmd = step(state, [box], p.dwell_ms, p)
    assert cmd is not None
    target = cmd.target
    assert target.y >= 0 and target.bottom <= p.source_h + 1e-6
    assert target.x >= 0 and target.right <= p.source_w + 1e-6


def test_crown_violation_forces_a_commit_inside_the_dead_zone():
    p = PolicyParams()
    committed = _target([Rect(800, 300, 300, 700)], p)  # today's plain commit, no crown
    state = PolicyState(current=committed, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)

    # Same box, well within the dead zone -- but its crown has risen above
    # what the already-applied crop still contains. Without the crown
    # check, this would sit in the dead zone and never re-commit.
    box = Rect(800, 300, 300, 700, crown=50.0)
    state, cmd = step(state, [box], 1.0, p)

    assert cmd is not None
    assert cmd.reason == "crown"
    assert cmd.target.y <= box.crown + 1e-9


def test_mode_changes_always_cut():
    # Exaggerated ease_ms: a cut can only happen deliberately, not by accident.
    p = PolicyParams(ease_ms=5000.0)
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)

    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, enter_cmd = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert enter_cmd is not None and state.mode == "split"
    assert enter_cmd.duration_ms == 0.0
    assert enter_cmd.frm is None and enter_cmd.frm_cells is None

    # Lose box_b for long enough that its track dies and split exits.
    t = p.split_enter_ms
    exit_cmd = None
    while t < p.split_enter_ms + p.track_hold_ms + p.dwell_ms + 2000.0:
        t += 200.0
        state, cmd = step(state, [box_a], t, p)
        if cmd is not None and state.mode == "single":
            exit_cmd = cmd
            break

    assert exit_cmd is not None
    assert exit_cmd.duration_ms == 0.0
    assert exit_cmd.frm is None and exit_cmd.frm_cells is None


# --- issue #2: split decided on fresh detections, not remembered tracks -----


def test_split_entry_ignores_a_stale_remembered_pair():
    # box_b goes stale (still alive) past split_enter_ms; entry must not
    # fire on the remembered pair -- only a fresh ready frame may start it.
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)

    state, cmd = step(state, [box_a, box_b], 0.0, p)
    assert cmd is None and state.mode == "single"
    state, cmd = step(state, [box_a, box_b], p.split_enter_ms - 100.0, p)
    assert cmd is None and state.mode == "single"

    state, cmd = step(state, [box_a], p.split_enter_ms + 200.0, p)
    assert cmd is None
    assert state.mode == "single"


def test_split_entry_resumes_after_one_missed_frame():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)

    state, cmd = step(state, [box_a, box_b], 0.0, p)  # countdown starts at t=0
    assert cmd is None and state.mode == "single"

    miss_1 = p.track_hold_ms * 0.3
    miss_2 = p.track_hold_ms * 0.6
    state, cmd = step(state, [box_a], miss_1, p)  # box_b stale but tracked: suspended
    assert cmd is None and state.mode == "single"

    state, cmd = step(state, [box_a], miss_2, p)  # still suspended, track still alive
    assert cmd is None and state.mode == "single"

    # First fresh ready frame at or after split_enter_ms from the start (t=0).
    state, cmd = step(state, [box_a, box_b], p.split_enter_ms + 100.0, p)
    assert cmd is not None
    assert state.mode == "split"


def test_split_matches_subjects_globally_not_by_slot_order():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)  # smaller cx -> slot 0 at entry
    box_b = Rect(1400, 200, 150, 700)  # larger cx -> slot 1
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, cmd = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert cmd is not None and state.mode == "split"

    # box_a drops out for one frame: slot-order-first greedy lets slot 0
    # steal box_b (the only remaining box) since it processes slot 0 first,
    # leaving slot 1 holding a stale copy of that same subject.
    t = p.split_enter_ms + 200.0
    state, cmd = step(state, [box_b], t, p)
    assert state.mode == "split"
    alive = [tr for tr in state.tracks if tr is not None]
    assert len(alive) == 2
    assert abs(alive[0].box.cx - alive[1].box.cx) > 10.0


def test_split_entry_matches_boxes_globally_despite_a_decoy():
    # Two live tracks already close together (established below split_ready)
    # at cx 491.2 and 743.2. A later fixed-order triple then makes the pair
    # far apart for real (114.6, 820.5) plus an unrelated third box
    # (1172.3): slot-order-first greedy pairs the two boxes on the right
    # instead (not split-ready), only the cheapest global assignment
    # recovers the genuinely far-apart pair.
    p = PolicyParams()

    def mk(cx: float) -> Rect:
        return Rect(cx - 75.0, 200.0, 150.0, 700.0)

    state = initial_state(p)
    state, cmd = step(state, [mk(491.2), mk(743.2)], 0.0, p)
    assert cmd is None and state.mode == "single"

    left, right_far, right_near = mk(114.6), mk(1172.3), mk(820.5)
    boxes = [left, right_far, right_near]
    t = 0.0
    entered = False
    for _ in range(10):
        t += 100.0
        state, cmd = step(state, boxes, t, p)
        if state.mode == "split":
            entered = True
            break

    assert entered
    assert cmd is not None and cmd.mode == "split"


def test_split_tracks_update_through_the_ease_lock():
    # track_hold_ms < ease_ms: without updating tracks during the lock, the
    # instant it lifts, last_seen_ms is still the pre-lock value and a
    # subject detected the whole time reads as gone for the full lock
    # duration -- one missed frame right after is then enough to kill it.
    p = PolicyParams(track_hold_ms=500.0, ease_ms=1000.0)
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, _ = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert state.mode == "split"

    moved_a = Rect(600, 200, 150, 700)
    t = p.split_enter_ms
    state, cmd = step(state, [moved_a, box_b], t, p)
    assert cmd is None and state.pending_cells is not None

    state, cmd = step(state, [moved_a, box_b], t + p.dwell_ms, p)
    assert cmd is not None
    busy_until = state.busy_until_ms
    assert busy_until is not None

    t = t + p.dwell_ms
    while True:  # both subjects detected throughout the lock
        t += 100.0
        if t >= busy_until:  # busy_until itself is already unlocked
            break
        state, _ = step(state, [moved_a, box_b], t, p)
        assert state.mode == "split"

    state, cmd = step(state, [moved_a], busy_until + 1.0, p)  # one missed frame
    assert state.mode == "split"


def test_split_exit_by_track_death_cuts_on_the_same_frame():
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, _ = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert state.mode == "split"

    t = p.split_enter_ms
    exit_cmd = None
    while t < p.split_enter_ms + p.track_hold_ms + 1000.0:
        t += 200.0
        state, cmd = step(state, [box_a], t, p)
        if state.mode == "single":
            exit_cmd = cmd
            break

    assert exit_cmd is not None
    assert exit_cmd.mode == "single"
    assert exit_cmd.duration_ms == 0.0
    assert exit_cmd.frm is None


def test_split_exit_by_not_ready_cuts_on_the_same_frame():
    # close_a/close_b are chosen so the single target for that pair sits
    # inside the dead zone of the union of cells: without an immediate cut,
    # the exit would never actually be applied (verified separately).
    p = PolicyParams()
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    close_a = Rect(810, 200, 150, 700)
    close_b = Rect(960, 200, 150, 700)
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, _ = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert state.mode == "split"

    t = p.split_enter_ms
    exit_cmd = None
    while t < p.split_enter_ms + p.split_exit_ms + 1000.0:
        t += 200.0
        state, cmd = step(state, [close_a, close_b], t, p)
        if state.mode == "single":
            exit_cmd = cmd
            break

    assert exit_cmd is not None
    assert exit_cmd.mode == "single"
    assert exit_cmd.duration_ms == 0.0
    assert exit_cmd.frm is None


# --- a due mode switch always cuts through an in-flight ease lock ----------


def _lock_in_single_mode(p: PolicyParams) -> PolicyState:
    steady = _target([Rect(800, 200, 300, 700)], p)
    moved_box = Rect(1200, 200, 300, 700)
    state = PolicyState(current=steady, pending=None, pending_since_ms=None, last_seen_ms=0.0, busy_until_ms=None)
    state, cmd = step(state, [moved_box], 0.0, p)
    state, cmd = step(state, [moved_box], p.dwell_ms, p)
    assert cmd is not None and state.busy_until_ms is not None
    return state


def test_mode_switch_cuts_during_the_lock():
    p = PolicyParams(ease_ms=5000.0)
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = _lock_in_single_mode(p)
    busy_until = state.busy_until_ms

    t = p.dwell_ms
    switched_at = None
    while t < busy_until - 1:
        t += 100.0
        state, cmd = step(state, [box_a, box_b], t, p)
        if state.mode == "split":
            switched_at = t
            break

    assert switched_at is not None
    assert switched_at < busy_until  # fired while the lock was still active


# --- crown constraint holds even during an in-flight ease lock -------------


def test_crown_violation_commits_through_the_lock_single():
    p = PolicyParams(ease_ms=5000.0)
    state = _lock_in_single_mode(p)
    busy_until = state.busy_until_ms

    # Same box the lock is easing to, but its crown has since risen above
    # the already-applied crop -- a hard constraint, not a preference the
    # lock should be allowed to postpone.
    risen = Rect(1200, 200, 300, 700, crown=50.0)
    t = p.dwell_ms + 10.0
    assert t < busy_until
    state, cmd = step(state, [risen], t, p)

    assert cmd is not None
    assert cmd.reason == "crown"
    assert cmd.target.y <= risen.crown + 1e-9


def test_crown_violation_commits_through_the_lock_split():
    p = PolicyParams(ease_ms=5000.0)
    box_a = Rect(300, 200, 150, 700)
    box_b = Rect(1400, 200, 150, 700)
    state = initial_state(p)
    state, _ = step(state, [box_a, box_b], 0.0, p)
    state, _ = step(state, [box_a, box_b], p.split_enter_ms, p)
    assert state.mode == "split"

    moved_a = Rect(600, 200, 150, 700)
    t = p.split_enter_ms
    state, _ = step(state, [moved_a, box_b], t, p)
    state, cmd = step(state, [moved_a, box_b], t + p.dwell_ms, p)
    assert cmd is not None and state.busy_until_ms is not None
    busy_until = state.busy_until_ms

    risen_a = Rect(600, 200, 150, 700, crown=50.0)
    t2 = t + p.dwell_ms + 10.0
    assert t2 < busy_until
    state, cmd = step(state, [risen_a, box_b], t2, p)

    assert cmd is not None
    assert cmd.reason == "crown"
    assert cmd.mode == "split"


def test_no_crown_violation_still_no_command_during_the_lock():
    p = PolicyParams(ease_ms=5000.0)
    state = _lock_in_single_mode(p)
    busy_until = state.busy_until_ms

    t = p.dwell_ms + 10.0
    assert t < busy_until
    state, cmd = step(state, [Rect(1200, 200, 300, 700)], t, p)

    assert cmd is None
