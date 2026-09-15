"""Deterministic corpus replay: same detector, same policy as scripts.run.

Drives core.policy.step with simulated time (frame.pts_ms), never a clock,
so two runs over the same clip and arguments are byte-identical.

Run as: uv run python -m scripts.corpus <clip> --out out.jsonl
Or replay an existing trace: uv run python -m scripts.corpus --from-trace in.jsonl --out out.jsonl
"""

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Iterator

from adapters.detector import build_detector, to_source_rect
from adapters.video import VideoFrames
from core.geometry import Rect, clamp_to_source, expand, fit_ratio, union
from core.policy import PolicyParams, PolicyState, height_floor, initial_state, split_ready, step
from scripts.layout import RATIO

_PLACEMENT_KEYS = {"between": "n_between", "on_subject": "n_on_subject", "elsewhere": "n_elsewhere", "overlap": "n_overlap"}

# Tolerance for a non-fresh-ready gap inside an otherwise continuous split
# episode; see the "entrée (trous tolérés)" metric in print_summary.
_EPISODE_GAP_MS = 1000.0


def parse_args() -> argparse.Namespace:
    defaults = PolicyParams()
    parser = argparse.ArgumentParser(description="Rejeu déterministe d'un clip à travers détecteur et politique.")
    parser.add_argument("clip", nargs="?", default=None, help="Vidéo à décoder (exclusif avec --from-trace).")
    parser.add_argument(
        "--from-trace", default=None, help="Rejoue les boîtes d'une trace JSONL existante plutôt que de décoder un clip."
    )
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--start", type=float, default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument(
        "--detector", choices=("pose", "vision", "yolo"), default="vision" if sys.platform == "darwin" else "yolo"
    )
    parser.add_argument("--upper-body", action="store_true")
    parser.add_argument("--yolo-model", default="models/yolo11m-pose.pt", help="Chemin du modèle yolo (.pt ou .engine).")
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary-json", default=None, help="Écrit aussi l'agrégat en JSON (voir tests/corpus/README.md).")
    parser.add_argument("--margin", type=float, default=defaults.margin)
    parser.add_argument("--min-crop-h", type=float, default=defaults.min_crop_h)
    parser.add_argument("--dead-zone", type=float, default=defaults.dead_zone)
    parser.add_argument("--dwell-ms", type=float, default=defaults.dwell_ms)
    parser.add_argument("--ease-ms", type=float, default=defaults.ease_ms)
    parser.add_argument("--snap", action="store_true")
    parser.add_argument("--hold-ms", type=float, default=defaults.hold_ms)
    parser.add_argument("--eye-line", type=float, default=defaults.eye_line)
    parser.add_argument("--zoom-dead-zone", type=float, default=defaults.zoom_dead_zone)
    parser.add_argument("--max-zoom", type=float, default=defaults.max_zoom)
    parser.add_argument(
        "--split",
        action=argparse.BooleanOptionalAction,
        default=defaults.split_enabled,
        help="Cadrage empilé à deux sujets (--no-split pour l'observer seul, hors politique split).",
    )
    parser.add_argument("--split-min-gap", type=float, default=defaults.split_min_gap)
    parser.add_argument("--split-enter-ms", type=float, default=defaults.split_enter_ms)
    parser.add_argument("--split-exit-ms", type=float, default=defaults.split_exit_ms)
    parser.add_argument("--track-hold-ms", type=float, default=defaults.track_hold_ms)
    args = parser.parse_args()
    if (args.clip is None) == (args.from_trace is None):
        parser.error("indiquez soit <clip>, soit --from-trace, jamais les deux ni aucun des deux.")
    if args.from_trace is not None and Path(args.out).resolve() == Path(args.from_trace).resolve():
        parser.error("--out ne peut pas être la trace lue par --from-trace : cela l'écraserait avant qu'elle ne soit rejouée.")
    return args


def sha256_16(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def rect_dict(r: Rect | None) -> dict | None:
    return None if r is None else {"x": r.x, "y": r.y, "w": r.w, "h": r.h}


def box_dicts(rects: list[Rect], scores: list[float]) -> list[dict]:
    return [
        {
            "x": r.x, "y": r.y, "w": r.w, "h": r.h, "score": s,
            "anchor": r.anchor, "crown": r.crown, "crown_margin": r.crown_margin, "bust": r.bust,
        }
        for r, s in zip(rects, scores)
    ]


def rect_from_box(b: dict) -> Rect:
    """Rebuild a Rect from one box_dicts row; a missing key (old-format
    trace) becomes None so full-yolo11m-pose-v1.jsonl still loads.
    """
    anchor, bust = b.get("anchor"), b.get("bust")
    return Rect(
        b["x"], b["y"], b["w"], b["h"],
        anchor=None if anchor is None else tuple(anchor),
        crown=b.get("crown"),
        crown_margin=b.get("crown_margin"),
        bust=None if bust is None else tuple(bust),
    )


def state_dict(s: PolicyState) -> dict:
    return {
        "current": rect_dict(s.current),
        "pending": rect_dict(s.pending),
        "pending_since_ms": s.pending_since_ms,
        "last_seen_ms": s.last_seen_ms,
        "busy_until_ms": s.busy_until_ms,
    }


def raw_target(merged: Rect | None, p: PolicyParams) -> Rect | None:
    """Mirrors core.policy's internal target formula, as scripts.run does."""
    if merged is None:
        return None
    return clamp_to_source(
        fit_ratio(expand(merged, p.margin), p.ratio), p.source_w, p.source_h, p.ratio, height_floor(p), p.eye_line
    )


def is_degenerate(merged: Rect | None, target: Rect | None, p: PolicyParams) -> bool:
    """True when the target is pinned rather than chosen: fit_ratio had to
    grow the union (its own aspect was wider than the target ratio) and
    clamp_to_source pinned the result to the source's full height.
    """
    if merged is None or target is None or merged.h <= 0:
        return False
    return (merged.w / merged.h) > p.ratio and target.h == p.source_h


def fresh_ready(boxes: list[Rect], p: PolicyParams) -> bool:
    """True when this frame's own detections (not remembered tracks) already
    justify a split: at least two boxes, and some pair of them split-ready.
    """
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if split_ready([boxes[i], boxes[j]], p):
                return True
    return False


def crop_vs_subjects(boxes: list[dict], crop_cx: float) -> str:
    """Where crop_cx falls: on the lone subject, between the two outermost
    detected ones, or elsewhere. "n/a" only when there is no detection.
    """
    if not boxes:
        return "n/a"
    if len(boxes) == 1:
        b = boxes[0]
        return "on_subject" if b["x"] <= crop_cx <= b["x"] + b["w"] else "elsewhere"
    ordered = sorted(boxes, key=lambda b: b["x"] + b["w"] / 2)
    left, right = ordered[0], ordered[-1]
    left_edge, right_edge = left["x"] + left["w"], right["x"]
    if right_edge <= left_edge:
        return "overlap"
    if left_edge <= crop_cx <= right_edge:
        return "between"
    on_left = left["x"] <= crop_cx <= left["x"] + left["w"]
    on_right = right["x"] <= crop_cx <= right["x"] + right["w"]
    return "on_subject" if (on_left or on_right) else "elsewhere"


def _crown_check(crown: float, applied_top: float) -> dict:
    """crown < 0 means the crown itself falls above the source: the one
    acceptable exception, reported apart from an actual cut.
    """
    margin = crown - applied_top
    unreachable = crown < 0
    return {"crown": crown, "margin": margin, "unreachable": unreachable, "cut": None if unreachable else margin < 0}


def crown_checks(state: PolicyState, boxes: list[Rect]) -> list[dict]:
    """Crown containment of the rect actually applied this frame, keyed off
    this frame's own detections -- never state.tracks, so the rule is the
    same for any policy that reshapes tracking.

    Split: a box is checked against the applied cells whose horizontal span
    contains its cx, keeping the best margin -- cells often overlap in x, and
    a head shown whole in one cell is not cut; a box in no cell is skipped. Single: checked against state.current when its span
    contains the box's cx.
    """
    if state.mode == "split" and state.cells is not None:
        checks = []
        for box in boxes:
            if box.crown is None:
                continue
            containing = [c for c in state.cells if c.x <= box.cx <= c.right]
            if not containing:
                continue
            cell = min(containing, key=lambda c: c.y)
            checks.append(_crown_check(box.crown, cell.y))
        return checks
    return [
        _crown_check(box.crown, state.current.y)
        for box in boxes
        if box.crown is not None and state.current.x <= box.cx <= state.current.right
    ]


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _new_split_metrics() -> dict:
    return {
        "switches": 0, "entries": 0, "stale_entries": 0, "short_splits": 0,
        "split_ms_total": 0.0, "entry_ms": [], "entry_episode_ms": [], "exit_ms": [],
        "exit_causes": {"track_died": 0, "not_ready": 0}, "phantom_exits": 0, "exit_apply_ms": [],
    }


def split_summary(m: dict, duration_ms: float) -> dict:
    return {
        "switches": m["switches"],
        "entries": m["entries"],
        "stale_entries": m["stale_entries"],
        "short_splits": m["short_splits"],
        "split_time_share": round(m["split_ms_total"] / duration_ms, 4) if duration_ms else 0.0,
        "entry_ms": {"median": round(percentile(m["entry_ms"], 0.5), 1), "p90": round(percentile(m["entry_ms"], 0.9), 1)},
        "entry_episode_ms": {
            "median": round(percentile(m["entry_episode_ms"], 0.5), 1),
            "p90": round(percentile(m["entry_episode_ms"], 0.9), 1),
        },
        "exit_ms": {"median": round(percentile(m["exit_ms"], 0.5), 1), "p90": round(percentile(m["exit_ms"], 0.9), 1)},
        "exit_causes": dict(m["exit_causes"]),
        "phantom_exits": m["phantom_exits"],
        "exit_apply_ms": {
            "median": round(percentile(m["exit_apply_ms"], 0.5), 1),
            "p90": round(percentile(m["exit_apply_ms"], 0.9), 1),
        },
    }


def print_summary(
    n_frames: int, n_detections: int, moves: list[dict], detector_name: str, degeneracy: dict, head: dict, split: dict
) -> None:
    rate = 100.0 * n_detections / n_frames if n_frames else 0.0
    print(f"\nDétecteur : {detector_name}")
    print(f"{n_frames} images rejouées, taux de détection {rate:.1f}% ({n_detections}/{n_frames}).")
    print(f"{len(moves)} commandes émises.")

    if len(moves) >= 2:
        intervals = [b["pts_ms"] - a["pts_ms"] for a, b in zip(moves, moves[1:])]
        print(f"Intervalle moyen entre commandes : {statistics.mean(intervals):.0f} ms.")
    else:
        print("Intervalle moyen entre commandes : n/a (moins de deux commandes).")

    if moves:
        px = [m["move_px"] for m in moves]
        print(
            f"Amplitude des recadrages (px) : min={min(px):.0f} "
            f"médiane={percentile(px, 0.5):.0f} p90={percentile(px, 0.9):.0f} max={max(px):.0f}"
        )
    else:
        print("Amplitude des recadrages : n/a (aucune commande).")

    n_degenerate = degeneracy["n_degenerate"]
    n_harmful = degeneracy["n_degenerate_harmful"]
    n_benign = degeneracy["n_degenerate_benign"]
    if n_detections:
        deg_rate = 100.0 * n_degenerate / n_detections
        harmful_rate = 100.0 * n_harmful / n_detections
        benign_rate = 100.0 * n_benign / n_detections
        print(f"Cible figée au maximum (degenerate) : {deg_rate:.1f}% des images avec détection ({n_degenerate}/{n_detections}).")
        # A 9:16 crop of 1920x1080 is at most 607.5px (31.6%) wide: any subject
        # wider than that pins the crop on its own, which is ordinary framing.
        print(f"  dont cadrage sur un sujet (bénin) : {benign_rate:.1f}% ({n_benign}/{n_detections})")
        print(f"  dont cadrage entre les sujets (problématique) : {harmful_rate:.1f}% ({n_harmful}/{n_detections})")
    else:
        harmful_rate = 0.0
        print("Cible figée au maximum (degenerate) : n/a (aucune détection).")

    n_multibody = degeneracy["n_multibody"]
    if n_multibody:
        between_pct = 100.0 * degeneracy["n_between"] / n_multibody
        on_subject_pct = 100.0 * degeneracy["n_on_subject"] / n_multibody
        elsewhere_pct = 100.0 * degeneracy["n_elsewhere"] / n_multibody
        overlap_pct = 100.0 * degeneracy["n_overlap"] / n_multibody
        print(
            f"Sur {n_multibody} images à 2+ corps détectés : centre entre les sujets {between_pct:.1f}%, "
            f"sur un sujet {on_subject_pct:.1f}%, ailleurs {elsewhere_pct:.1f}%, "
            f"boîtes en chevauchement {overlap_pct:.1f}%."
        )
    else:
        print("Images à 2+ corps détectés : n/a (aucune).")

    if n_detections and harmful_rate > 50.0:
        print(
            "Attention : un cadrage entre les sujets élevé avec peu de commandes émises signifie "
            "que la politique n'a pas pu choisir de cadrage, pas qu'elle est restée stable."
        )

    n_checked = head["n_checked"]
    if n_checked:
        cut_rate = 100.0 * head["n_cut"] / n_checked
        print(f"Crâne coupé (cadre appliqué) : {cut_rate:.1f}% ({head['n_cut']}/{n_checked}).")
        margins = head["margins"]
        print(f"  marge crâne/bord haut : médiane {percentile(margins, 0.5):.0f}px, p10 {percentile(margins, 0.1):.0f}px")
        print(f"  dont crâne hors source (cas accepté, exclu ci-dessus) : {head['n_unreachable']}")
    else:
        print("Crâne coupé : n/a (détecteur sans estimation de crâne, ou aucun sujet exploitable).")

    if split["switches"]:
        print(
            f"Split (bascules réellement appliquées à OBS) : {split['switches']} bascules, "
            f"{split['entries']} entrées ({split['stale_entries']} périmées), {split['short_splits']} splits < 1,5 s."
        )
        print(f"  part du temps en split : {100 * split['split_time_share']:.1f}%")
        print(
            f"  entrée depuis détections fraîches : médiane {split['entry_ms']['median']:.0f} ms, "
            f"p90 {split['entry_ms']['p90']:.0f} ms"
        )
        print(
            f"  entrée (trous courts tolérés) : médiane {split['entry_episode_ms']['median']:.0f} ms, "
            f"p90 {split['entry_episode_ms']['p90']:.0f} ms"
        )
        print(
            f"  sortie depuis la dernière détection fraîche : médiane {split['exit_ms']['median']:.0f} ms, "
            f"p90 {split['exit_ms']['p90']:.0f} ms"
        )
        causes = split["exit_causes"]
        print(f"  causes de sortie (état de la politique) : piste éteinte {causes['track_died']}, plus prête {causes['not_ready']}")
        print(
            f"  sorties d'état jamais appliquées avant un retour en split (diagnostic) : {split['phantom_exits']}"
        )
        print(
            f"  délai état → commande appliquée pour une sortie (diagnostic) : "
            f"médiane {split['exit_apply_ms']['median']:.0f} ms, p90 {split['exit_apply_ms']['p90']:.0f} ms"
        )
    else:
        print("Split : n/a (aucune bascule).")


def summary_dict(
    header: dict, n_frames: int, n_detections: int, moves: list[dict], degeneracy: dict, head: dict, split: dict
) -> dict:
    """The core of what print_summary prints, as JSON. tests/corpus/README.md
    documents how classement_du_centre and largeur_union_2corps_px (from
    tests/corpus/tools/analyze_two_subjects.py against the trace) fold in.
    """
    n_multibody = degeneracy["n_multibody"]
    result = {
        "source": header,
        "images": n_frames,
        "taux_detection": round(n_detections / n_frames, 4) if n_frames else 0.0,
        "commandes": len(moves),
        "images_2_corps_ou_plus": n_multibody,
        "part_2_corps": round(n_multibody / n_frames, 4) if n_frames else 0.0,
        "degenerate": round(degeneracy["n_degenerate"] / n_detections, 4) if n_detections else 0.0,
    }
    if head["n_checked"]:
        result["crane_coupe"] = {
            "cellules_verifiees": head["n_checked"],
            "taux_coupe": round(head["n_cut"] / head["n_checked"], 4),
            "marge_mediane_px": round(percentile(head["margins"], 0.5), 1),
            "marge_p10_px": round(percentile(head["margins"], 0.1), 1),
            "crane_hors_source_exclu": head["n_unreachable"],
        }
    result["split"] = split
    return result


def live_frames(video: VideoFrames, detector, source_w: int, source_h: int) -> Iterator[tuple[int, float, list[Rect], list[float]]]:
    for frame in video:
        boxes = detector.detect(frame.jpeg)
        rects = [to_source_rect(b, source_w, source_h) for b in boxes]
        yield frame.index, frame.pts_ms, rects, [b.score for b in boxes]


def load_trace_header(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.loads(f.readline())


def trace_frames(path: str) -> Iterator[tuple[int, float, list[Rect], list[float]]]:
    with open(path, encoding="utf-8") as f:
        f.readline()  # header, already read by load_trace_header
        for line in f:
            row = json.loads(line)
            boxes = row["boxes"]
            yield row["index"], row["pts_ms"], [rect_from_box(b) for b in boxes], [b["score"] for b in boxes]


def run_loop(
    frames: Iterator[tuple[int, float, list[Rect], list[float]]], p: PolicyParams, out
) -> tuple[int, int, list[dict], dict, dict, dict]:
    """Feed frames through the policy, writing one trace line each and
    accumulating every summary statistic. The one code path for both a live
    detector run and a --from-trace replay.
    """
    state = initial_state(p)
    n_frames = 0
    n_detections = 0
    moves: list[dict] = []
    degeneracy = {
        "n_degenerate": 0, "n_degenerate_benign": 0, "n_degenerate_harmful": 0,
        "n_multibody": 0, "n_between": 0, "n_on_subject": 0, "n_elsewhere": 0, "n_overlap": 0,
    }
    head: dict = {"n_checked": 0, "n_cut": 0, "n_unreachable": 0, "margins": []}
    split = _new_split_metrics()

    fresh_since = fresh_until = episode_since = episode_last = split_start_ms = None
    pending_exit_since_ms = None
    applied_mode = "single"
    last_pts_ms = 0.0

    for index, pts_ms, rects, scores in frames:
        box_list = box_dicts(rects, scores)
        merged = union(rects)
        target = raw_target(merged, p)
        degenerate = is_degenerate(merged, target, p)

        fresh = fresh_ready(rects, p)
        if fresh:
            fresh_since = pts_ms if fresh_since is None else fresh_since
            fresh_until = pts_ms
            episode_since = pts_ms if episode_since is None else episode_since
            episode_last = pts_ms
        else:
            fresh_since = None
            if episode_since is not None and pts_ms - episode_last >= _EPISODE_GAP_MS:
                episode_since = None

        prev_state = state
        state, cmd = step(state, rects, pts_ms, p)

        if cmd is not None:
            move_px = math.hypot(cmd.target.cx - prev_state.current.cx, cmd.target.cy - prev_state.current.cy)
            moves.append({"pts_ms": pts_ms, "move_px": move_px})

        # State-layer diagnostic: a split->single transition here is only a
        # candidate exit until an actual command applies it -- see below.
        if state.mode != prev_state.mode:
            if state.mode == "single":
                if pending_exit_since_ms is not None:
                    split["phantom_exits"] += 1
                pending_exit_since_ms = pts_ms
                alive = [t for t in state.tracks if t is not None]
                prev_alive = [t for t in prev_state.tracks if t is not None]
                cause = "track_died" if len(alive) < 2 or len(prev_alive) < 2 else "not_ready"
                split["exit_causes"][cause] += 1
            elif pending_exit_since_ms is not None:
                split["phantom_exits"] += 1
                pending_exit_since_ms = None

        if cmd is not None and cmd.mode != applied_mode:
            split["switches"] += 1
            if cmd.mode == "split":
                split["entries"] += 1
                split_start_ms = pts_ms
                alive = [t for t in state.tracks if t is not None]
                if any(t.last_seen_ms != pts_ms for t in alive):
                    split["stale_entries"] += 1
                if fresh_since is not None:
                    split["entry_ms"].append(pts_ms - fresh_since)
                if episode_since is not None:
                    split["entry_episode_ms"].append(pts_ms - episode_since)
            else:
                if split_start_ms is not None:
                    duration = pts_ms - split_start_ms
                    split["split_ms_total"] += duration
                    split["short_splits"] += 1 if duration < 1500.0 else 0
                    split_start_ms = None
                if fresh_until is not None:
                    split["exit_ms"].append(pts_ms - fresh_until)
                if pending_exit_since_ms is not None:
                    split["exit_apply_ms"].append(pts_ms - pending_exit_since_ms)
                    pending_exit_since_ms = None
            applied_mode = cmd.mode

        placement = crop_vs_subjects(box_list, target.cx) if target is not None else "n/a"
        checks = crown_checks(state, rects)

        entry = {
            "index": index,
            "pts_ms": pts_ms,
            "n_detections": len(rects),
            "boxes": box_list,
            "union": rect_dict(merged),
            "target": rect_dict(target),
            "degenerate": degenerate,
            "crop_vs_subjects": placement,
            "state": state_dict(state),
            "emitted": rect_dict(cmd.target) if cmd is not None else None,
            "crown_checks": checks,
        }
        out.write(json.dumps(entry) + "\n")

        n_frames += 1
        n_detections += 1 if rects else 0
        if degenerate:
            degeneracy["n_degenerate"] += 1
            degeneracy["n_degenerate_harmful" if placement == "between" else "n_degenerate_benign"] += 1
        if len(rects) >= 2:
            degeneracy["n_multibody"] += 1
            degeneracy[_PLACEMENT_KEYS[placement]] += 1
        for check in checks:
            if check["unreachable"]:
                head["n_unreachable"] += 1
                continue
            head["n_checked"] += 1
            head["margins"].append(check["margin"])
            head["n_cut"] += 1 if check["cut"] else 0

        last_pts_ms = pts_ms

    if split_start_ms is not None:
        # Right-censored: this split never exits within the trace, so its
        # true duration is unknown and it cannot count as short.
        split["split_ms_total"] += last_pts_ms - split_start_ms

    return n_frames, n_detections, moves, degeneracy, head, split_summary(split, last_pts_ms)


def main() -> None:
    args = parse_args()

    if args.from_trace is not None:
        trace_header = load_trace_header(args.from_trace)
        probe = trace_header["probe"]
        detector_name = trace_header["detector"]
        frames = trace_frames(args.from_trace)
    else:
        video = VideoFrames(args.clip, fps=args.fps, width=args.width, start_s=args.start, duration_s=args.duration)
        probe = video.probe()
        if args.yolo_model.endswith(".engine"):
            print(
                "Attention : un moteur TensorRT n'est pas garanti déterministe et est lié à ce "
                "GPU/pilote, alors que la référence du corpus est produite en .pt."
            )
        detector = build_detector(args.detector, args.upper_body, args.yolo_model)
        detector_name = detector.name
        frames = live_frames(video, detector, probe["width"], probe["height"])

    p = PolicyParams(
        source_w=probe["width"],
        source_h=probe["height"],
        ratio=RATIO,
        margin=args.margin,
        min_crop_h=args.min_crop_h,
        dead_zone=args.dead_zone,
        dwell_ms=args.dwell_ms,
        ease_ms=args.ease_ms,
        snap=args.snap,
        hold_ms=args.hold_ms,
        eye_line=args.eye_line,
        zoom_dead_zone=args.zoom_dead_zone,
        max_zoom=args.max_zoom,
        split_enabled=args.split,
        split_min_gap=args.split_min_gap,
        split_enter_ms=args.split_enter_ms,
        split_exit_ms=args.split_exit_ms,
        track_hold_ms=args.track_hold_ms,
    )

    if args.from_trace is not None:
        replay_params = {name: getattr(p, name) for name in p.__dataclass_fields__}
        diffs = [
            f"{name} {trace_header['params'].get(name)} → {replay_params[name]}"
            for name in p.__dataclass_fields__
            if trace_header["params"].get(name) != replay_params[name]
        ]
        if diffs:
            print("Attention : paramètres différents de ceux de la trace : " + ", ".join(diffs))
        header = {**trace_header, "params": replay_params}
    else:
        header = {
            "clip_sha256_16": sha256_16(args.clip),
            "probe": probe,
            "params": {name: getattr(p, name) for name in p.__dataclass_fields__},
            "detector": detector_name,
            "yolo_model": getattr(detector, "model_path", None),
            "yolo_imgsz": getattr(detector, "imgsz", None),
            "yolo_conf": getattr(detector, "conf", None),
            "fps": args.fps,
            # probe["fps"] is the source's native rate; fps is what we sample it
            # at, so decimation says how many source frames each sample skips.
            "decimation": probe["fps"] / args.fps,
            "width": args.width,
            "start_s": args.start,
            "duration_s": args.duration,
        }

    with open(args.out, "w", newline="\n") as out:
        out.write(json.dumps(header) + "\n")
        n_frames, n_detections, moves, degeneracy, head, split = run_loop(frames, p, out)

    print_summary(n_frames, n_detections, moves, detector_name, degeneracy, head, split)
    if args.summary_json:
        with open(args.summary_json, "w", newline="\n") as f:
            json.dump(summary_dict(header, n_frames, n_detections, moves, degeneracy, head, split), f, indent=2)
            f.write("\n")


if __name__ == "__main__":
    main()
