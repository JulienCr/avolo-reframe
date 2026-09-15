"""Pure framing policy: (detections + state) -> crop rectangle.

No I/O, no clock reads. Time enters only as the now_ms argument.
"""

from dataclasses import dataclass, replace
from itertools import permutations

from core.geometry import Rect, clamp_to_source, default_rect, expand, fit_ratio, union


@dataclass(frozen=True)
class PolicyParams:
    source_w: int = 1920
    source_h: int = 1080
    ratio: float = 0.5625
    margin: float = 0.18
    min_crop_h: float = 960.0
    dead_zone: float = 0.12
    dwell_ms: float = 400.0
    ease_ms: float = 320.0
    snap: bool = False
    hold_ms: float = 1500.0
    split_enabled: bool = True
    split_min_gap: float = 0.15
    split_enter_ms: float = 600.0
    # track_hold_ms and split_exit_ms both sit above the measured 4850 ms
    # mean dropout stretch for a lost subject: shorter values would
    # recreate the single/split oscillation this feature removes.
    split_exit_ms: float = 3000.0
    track_hold_ms: float = 6000.0
    # Temporary measurement variant: lets a due mode switch cut through an
    # in-flight ease lock instead of waiting for it to end. Off by default.
    mode_switch_through_ease: bool = False
    # A third down from the top; a starting value to tune, not a fixed law.
    eye_line: float = 0.33
    max_zoom: float = 1.5
    # Roughly double dead_zone: a zoom reads far more visibly than an equal pan.
    zoom_dead_zone: float = 0.25


@dataclass(frozen=True)
class Command:
    target: Rect
    frm: Rect | None
    duration_ms: float
    reason: str
    mode: str = "single"
    cells: tuple[Rect, Rect] | None = None
    frm_cells: tuple[Rect, Rect] | None = None


@dataclass(frozen=True)
class Track:
    box: Rect
    last_seen_ms: float


@dataclass(frozen=True)
class PolicyState:
    current: Rect
    pending: Rect | None
    pending_since_ms: float | None
    last_seen_ms: float | None
    busy_until_ms: float | None
    mode: str = "single"
    split_enter_since_ms: float | None = None
    split_exit_since_ms: float | None = None
    tracks: tuple[Track | None, Track | None] = (None, None)
    cells: tuple[Rect, Rect] | None = None
    pending_cells: tuple[Rect, Rect] | None = None
    # Set on a split->single reset, consumed (and cleared) by the commit
    # that follows -- possibly several dwelling frames later, hence a
    # state field rather than a one-call parameter.
    mode_cut_pending: bool = False


def initial_state(p: PolicyParams) -> PolicyState:
    return PolicyState(
        current=default_rect(p.source_w, p.source_h, p.ratio),
        pending=None,
        pending_since_ms=None,
        last_seen_ms=None,
        busy_until_ms=None,
    )


def height_floor(p: PolicyParams) -> float:
    """min_crop_h and max_zoom both floor the crop height; the larger of
    the two wins, so the tighter constraint is the one that actually holds.
    """
    return max(p.min_crop_h, p.source_h / p.max_zoom)


def _target_from_boxes(boxes: list[Rect], p: PolicyParams) -> Rect:
    merged = union(boxes)
    return clamp_to_source(
        fit_ratio(expand(merged, p.margin), p.ratio),
        p.source_w,
        p.source_h,
        p.ratio,
        height_floor(p),
        p.eye_line,
    )


def _moved(target: Rect, current: Rect, dead_zone: float, zoom_dead_zone: float) -> bool:
    dx = abs(target.cx - current.cx) / current.w
    dy = abs(target.cy - current.cy) / current.h
    ds = abs(target.h - current.h) / current.h
    return dx > dead_zone or dy > dead_zone or ds > zoom_dead_zone


def _crown_violated(current: Rect, target: Rect) -> bool:
    """True when current no longer contains target's crown AND recommitting
    would actually help -- without the second half, an unreachable crown
    (subject already at the very top of source) would force a commit,
    land at the same clamped position, and immediately violate again.
    """
    if target.crown is None:
        return False
    return target.crown < current.y + (target.crown_margin or 0.0) and target.y < current.y


def _gap_fraction(a: Rect, b: Rect, source_w: float) -> float:
    """Horizontal gap between two boxes, as a fraction of source width."""
    left, right = (a, b) if a.cx <= b.cx else (b, a)
    return (right.x - left.right) / source_w


def split_ready(boxes: list[Rect], p: PolicyParams) -> bool:
    """True when the single-mode target is degenerate and the two subjects
    are far enough apart that stacking them beats one shared crop.

    Degenerate mirrors scripts.corpus.is_degenerate: fit_ratio had to grow
    the union past the target ratio, and clamp_to_source pinned it to the
    full source height.
    """
    merged = union(boxes)
    target = clamp_to_source(
        fit_ratio(expand(merged, p.margin), p.ratio), p.source_w, p.source_h, p.ratio, height_floor(p), p.eye_line
    )
    degenerate = (merged.w / merged.h) > p.ratio and target.h == p.source_h
    return degenerate and _gap_fraction(boxes[0], boxes[1], p.source_w) > p.split_min_gap


def _cell_source(box: Rect) -> Rect:
    """The bust when the detector offers one, else the full box.

    A cell is landscape while a full body is portrait; fitting the full
    box would upscale the subject far past the cell, so the bust (which
    shares the cell's own shape) is used instead when available.
    """
    if box.bust is None:
        return box
    bx, by, bw, bh = box.bust
    return Rect(bx, by, bw, bh, anchor=box.anchor, crown=box.crown, crown_margin=box.crown_margin)


def _cell_rects(alive: list[Track], p: PolicyParams) -> tuple[Rect, Rect]:
    """Fit each tracked subject to a stacked cell: smaller centre x on top."""
    top, bottom = sorted(alive, key=lambda t: t.box.cx)
    # Cell height is half the output height at the same width, so the
    # cell ratio is double the single-crop ratio.
    cell_ratio = p.ratio * 2
    cell_min_h = height_floor(p) / 2
    return tuple(
        clamp_to_source(
            fit_ratio(expand(_cell_source(t.box), p.margin), cell_ratio),
            p.source_w,
            p.source_h,
            cell_ratio,
            cell_min_h,
            p.eye_line,
        )
        for t in (top, bottom)
    )


def _match_two_live(
    remaining: list[Rect], updated: list[Track | None], i0: int, i1: int, now_ms: float
) -> list[Rect]:
    """Assign remaining boxes to two already-live slots by lowest summed
    |Δcx|, not slot 0's own nearest pick first: a slot-order-first greedy
    can let slot 0 steal the box slot 1 actually needs.
    """
    if len(remaining) == 1:
        box = remaining[0]
        i = i0 if abs(box.cx - updated[i0].box.cx) <= abs(box.cx - updated[i1].box.cx) else i1
        updated[i] = Track(box, now_ms)
        return []

    def cost(pair: tuple[int, int]) -> float:
        a, b = pair
        return abs(remaining[a].cx - updated[i0].box.cx) + abs(remaining[b].cx - updated[i1].box.cx)

    best = min(permutations(range(len(remaining)), 2), key=cost)
    updated[i0] = Track(remaining[best[0]], now_ms)
    updated[i1] = Track(remaining[best[1]], now_ms)
    return [b for k, b in enumerate(remaining) if k not in best]


def _update_tracks(
    tracks: tuple[Track | None, Track | None], boxes: list[Rect], now_ms: float, p: PolicyParams
) -> tuple[Track | None, Track | None]:
    """Match boxes to the two tracking slots by nearest centre x.

    An unmatched slot keeps its last box for track_hold_ms, then dies:
    this is what lets split survive a subject Vision drops briefly.
    """
    remaining = list(boxes)
    updated = list(tracks)
    live = [i for i, t in enumerate(updated) if t is not None]

    if len(live) == 2 and remaining:
        remaining = _match_two_live(remaining, updated, live[0], live[1], now_ms)
    else:
        for i, track in enumerate(updated):
            if track is None or not remaining:
                continue
            nearest = min(remaining, key=lambda b: abs(b.cx - track.box.cx))
            updated[i] = Track(nearest, now_ms)
            remaining.remove(nearest)

    for i, track in enumerate(updated):
        if track is None and remaining:
            updated[i] = Track(remaining.pop(0), now_ms)

    return tuple(
        None if t is not None and now_ms - t.last_seen_ms >= p.track_hold_ms else t for t in updated
    )


def _enter_split(state: PolicyState, alive: list[Track], now_ms: float, p: PolicyParams) -> tuple[PolicyState, Command]:
    cells = _cell_rects(alive, p)
    target = union(list(cells))
    # A 1-rect-to-2-cell shape change has no natural interpolation, so entry
    # cuts directly, and so does the eventual exit (mode_cut_pending); only
    # a move within one mode ever eases.
    new_state = replace(
        state,
        current=target,
        pending=None,
        pending_since_ms=None,
        busy_until_ms=None,
        mode="split",
        split_enter_since_ms=None,
        split_exit_since_ms=None,
        cells=cells,
        pending_cells=None,
        mode_cut_pending=False,
    )
    command = Command(target=target, frm=None, duration_ms=0.0, reason="split", mode="split", cells=cells, frm_cells=None)
    return new_state, command


def _commit_split(
    state: PolicyState,
    cells: tuple[Rect, Rect],
    current_cells: tuple[Rect, Rect],
    now_ms: float,
    exit_since_ms: float | None,
    reason: str,
    p: PolicyParams,
) -> tuple[PolicyState, Command]:
    target = union(list(cells))
    if p.snap or p.ease_ms <= 0:
        command = Command(
            target=target, frm=None, duration_ms=0.0, reason=reason, mode="split", cells=cells, frm_cells=current_cells
        )
        busy_until_ms = None
    else:
        frm = union(list(current_cells))
        command = Command(
            target=target, frm=frm, duration_ms=p.ease_ms, reason=reason, mode="split", cells=cells, frm_cells=current_cells
        )
        busy_until_ms = now_ms + p.ease_ms

    committed = replace(
        state,
        current=target,
        cells=cells,
        pending_cells=None,
        pending_since_ms=None,
        busy_until_ms=busy_until_ms,
        split_exit_since_ms=exit_since_ms,
    )
    return committed, command


def _split_hold(
    state: PolicyState, alive: list[Track], exit_since_ms: float | None, now_ms: float, p: PolicyParams
) -> tuple[PolicyState, Command | None]:
    """Recompute cells against the already-applied state.cells, gated by the
    same dead-zone/dwell/ease discipline _step_single applies to a single
    crop: without it, split re-commits every frame even on a static scene.
    A crown violation on either cell bypasses that gate entirely.
    """
    cells = _cell_rects(alive, p)
    current_cells = state.cells

    if _crown_violated(current_cells[0], cells[0]) or _crown_violated(current_cells[1], cells[1]):
        return _commit_split(state, cells, current_cells, now_ms, exit_since_ms, "crown", p)

    if not (
        _moved(cells[0], current_cells[0], p.dead_zone, p.zoom_dead_zone)
        or _moved(cells[1], current_cells[1], p.dead_zone, p.zoom_dead_zone)
    ):
        cleared = replace(state, pending_cells=None, pending_since_ms=None, busy_until_ms=None, split_exit_since_ms=exit_since_ms)
        return cleared, None

    if state.pending_cells is None:
        started = replace(
            state, pending_cells=cells, pending_since_ms=now_ms, busy_until_ms=None, split_exit_since_ms=exit_since_ms
        )
        return started, None

    if now_ms - state.pending_since_ms < p.dwell_ms:
        waiting = replace(state, pending_cells=cells, busy_until_ms=None, split_exit_since_ms=exit_since_ms)
        return waiting, None

    return _commit_split(state, cells, current_cells, now_ms, exit_since_ms, "split", p)


def _reset_to_single(state: PolicyState) -> PolicyState:
    return replace(
        state,
        mode="single",
        split_enter_since_ms=None,
        split_exit_since_ms=None,
        cells=None,
        pending_cells=None,
        mode_cut_pending=True,
    )


def _commit(
    state: PolicyState,
    target: Rect,
    current: Rect,
    last_seen_ms: float | None,
    now_ms: float,
    reason: str,
    p: PolicyParams,
) -> tuple[PolicyState, Command]:
    # A mode change has nothing to interpolate from (single <-> split changes
    # rect count), so it always cuts regardless of ease_ms/snap. The flag may
    # have been set frames ago -- dwell can hold off the commit that clears it.
    if state.mode_cut_pending or p.snap or p.ease_ms <= 0:
        command = Command(target=target, frm=None, duration_ms=0.0, reason=reason)
        busy_until_ms = None
    else:
        command = Command(target=target, frm=current, duration_ms=p.ease_ms, reason=reason)
        busy_until_ms = now_ms + p.ease_ms

    committed = replace(
        state,
        current=target,
        pending=None,
        pending_since_ms=None,
        last_seen_ms=last_seen_ms,
        busy_until_ms=busy_until_ms,
        mode_cut_pending=False,
    )
    return committed, command


def _step_single(
    state: PolicyState, boxes: list[Rect], now_ms: float, p: PolicyParams
) -> tuple[PolicyState, Command | None]:
    # A split->single reset must cut on this very frame (see _reset_to_single):
    # the crown/dead-zone/dwell dance below is for in-mode moves, and would
    # otherwise defer or even swallow an exit that the state already committed to.
    if state.mode_cut_pending:
        if boxes:
            target = _target_from_boxes(boxes, p)
            last_seen_ms = now_ms
        else:
            alive_boxes = [t.box for t in state.tracks if t is not None]
            if alive_boxes:
                target = _target_from_boxes(alive_boxes, p)
            else:
                target = default_rect(p.source_w, p.source_h, p.ratio)
            last_seen_ms = state.last_seen_ms
        return _commit(state, target, state.current, last_seen_ms, now_ms, "exit", p)

    current = state.current
    last_seen_ms = state.last_seen_ms
    reason = "commit"
    zoom_dead_zone = p.zoom_dead_zone

    if boxes:
        target = _target_from_boxes(boxes, p)
        last_seen_ms = now_ms
        # A cut head bypasses the dead zone and the dwell entirely: this
        # is a hard constraint, not a preference the dead zone should damp.
        if _crown_violated(current, target):
            return _commit(state, target, current, last_seen_ms, now_ms, "crown", p)
    elif last_seen_ms is not None and now_ms - last_seen_ms < p.hold_ms:
        held = replace(state, last_seen_ms=last_seen_ms, busy_until_ms=None)
        return held, None
    else:
        target = default_rect(p.source_w, p.source_h, p.ratio)
        reason = "lost"
        # A vanished subject is a real cut, not detection noise: the wider
        # zoom hysteresis would otherwise delay widening back out.
        zoom_dead_zone = p.dead_zone

    if not _moved(target, current, p.dead_zone, zoom_dead_zone):
        cleared = replace(state, pending=None, pending_since_ms=None, last_seen_ms=last_seen_ms, busy_until_ms=None)
        return cleared, None

    pending_since_ms = state.pending_since_ms
    if state.pending is None:
        started = replace(state, pending=target, pending_since_ms=now_ms, last_seen_ms=last_seen_ms, busy_until_ms=None)
        return started, None

    # Refresh the drifting target but keep the original departure time:
    # that timestamp is the dwell countdown, and only a return to the
    # dead zone (handled above) is allowed to reset it.
    if now_ms - pending_since_ms < p.dwell_ms:
        waiting = replace(state, pending=target, last_seen_ms=last_seen_ms, busy_until_ms=None)
        return waiting, None

    return _commit(state, target, current, last_seen_ms, now_ms, reason, p)


def _locked_crown_command(
    state: PolicyState, boxes: list[Rect], now_ms: float, p: PolicyParams
) -> tuple[PolicyState, Command] | None:
    """Crown override for a frame the lock would otherwise swallow: the
    crown is a hard constraint everywhere else in the policy, so a playing
    ease must not be allowed to sit on a violation until it finishes.
    """
    if state.mode == "split" and state.cells is not None:
        alive = [t for t in state.tracks if t is not None]
        if len(alive) == 2:
            cells = _cell_rects(alive, p)
            if _crown_violated(state.cells[0], cells[0]) or _crown_violated(state.cells[1], cells[1]):
                return _commit_split(state, cells, state.cells, now_ms, state.split_exit_since_ms, "crown", p)
        return None
    if boxes:
        target = _target_from_boxes(boxes, p)
        if _crown_violated(state.current, target):
            return _commit(state, target, state.current, now_ms, now_ms, "crown", p)
    return None


def _locked_step(
    state: PolicyState, boxes: list[Rect], now_ms: float, p: PolicyParams
) -> tuple[PolicyState, Command | None]:
    """What a locked frame returns absent a due mode switch: the applied
    geometry stays put unless the crown constraint forces a commit.
    """
    crown = _locked_crown_command(state, boxes, now_ms, p)
    return crown if crown is not None else (state, None)


def step(
    state: PolicyState, boxes: list[Rect], now_ms: float, p: PolicyParams
) -> tuple[PolicyState, Command | None]:
    locked = state.busy_until_ms is not None and now_ms < state.busy_until_ms
    if locked and not p.mode_switch_through_ease:
        # Tracks keep ageing through the lock: otherwise a subject present
        # the whole time gets declared lost the instant the lock lifts (its
        # last_seen_ms would have been frozen at the pre-lock value).
        tracked = replace(state, tracks=_update_tracks(state.tracks, boxes, now_ms, p))
        return _locked_step(tracked, boxes, now_ms, p)

    if not p.split_enabled:
        if locked:
            return _locked_step(state, boxes, now_ms, p)
        return _step_single(state, boxes, now_ms, p)

    tracks = _update_tracks(state.tracks, boxes, now_ms, p)
    alive = [t for t in tracks if t is not None]
    ready = len(alive) == 2 and split_ready([t.box for t in alive], p)
    tracked = replace(state, tracks=tracks)

    if state.mode == "split":
        if len(alive) < 2:
            return _step_single(_reset_to_single(tracked), boxes, now_ms, p)
        if ready:
            if locked:
                return _locked_step(replace(tracked, split_exit_since_ms=None), boxes, now_ms, p)
            return _split_hold(tracked, alive, None, now_ms, p)
        since = state.split_exit_since_ms if state.split_exit_since_ms is not None else now_ms
        if now_ms - since >= p.split_exit_ms:
            return _step_single(_reset_to_single(tracked), boxes, now_ms, p)
        if locked:
            return _locked_step(replace(tracked, split_exit_since_ms=since), boxes, now_ms, p)
        return _split_hold(tracked, alive, since, now_ms, p)

    if not ready:
        if locked:
            return _locked_step(replace(tracked, split_enter_since_ms=None), boxes, now_ms, p)
        if state.split_enter_since_ms is not None:
            tracked = replace(tracked, split_enter_since_ms=None)
        return _step_single(tracked, boxes, now_ms, p)

    # Entry may only fire from a frame where both tracks are fresh: a
    # remembered pair can still justify staying in split (above), but not
    # starting it -- so a stale-but-ready pair merely suspends the countdown.
    fresh_both = all(t.last_seen_ms == now_ms for t in alive)
    if not fresh_both:
        if locked:
            return _locked_step(tracked, boxes, now_ms, p)
        return _step_single(tracked, boxes, now_ms, p)

    since = state.split_enter_since_ms if state.split_enter_since_ms is not None else now_ms
    if now_ms - since >= p.split_enter_ms:
        return _enter_split(tracked, alive, now_ms, p)
    if locked:
        return _locked_step(replace(tracked, split_enter_since_ms=since), boxes, now_ms, p)
    return _step_single(replace(tracked, split_enter_since_ms=since), boxes, now_ms, p)
