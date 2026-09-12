"""Deterministic corpus replay: same detector, same policy as scripts.run.

Drives core.policy.step with simulated time (frame.pts_ms), never a clock,
so two runs over the same clip and arguments are byte-identical.

Run as: uv run python -m scripts.corpus <clip> --out out.jsonl
"""

import argparse
import hashlib
import json
import math
import statistics

from adapters.detect_vision import VisionDetector
from adapters.detector import Detector, to_source_rect
from adapters.video import VideoFrames
from core.geometry import Rect, clamp_to_source, expand, fit_ratio, union
from core.policy import PolicyParams, PolicyState, initial_state, step
from scripts.layout import RATIO

_PLACEMENT_KEYS = {"between": "n_between", "on_subject": "n_on_subject", "elsewhere": "n_elsewhere", "overlap": "n_overlap"}


def parse_args() -> argparse.Namespace:
    defaults = PolicyParams()
    parser = argparse.ArgumentParser(description="Rejeu déterministe d'un clip à travers détecteur et politique.")
    parser.add_argument("clip")
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--start", type=float, default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--detector", choices=("pose", "vision"), default="vision")
    parser.add_argument("--upper-body", action="store_true")
    parser.add_argument("--out", required=True)
    parser.add_argument("--margin", type=float, default=defaults.margin)
    parser.add_argument("--min-crop-h", type=float, default=defaults.min_crop_h)
    parser.add_argument("--dead-zone", type=float, default=defaults.dead_zone)
    parser.add_argument("--dwell-ms", type=float, default=defaults.dwell_ms)
    parser.add_argument("--ease-ms", type=float, default=defaults.ease_ms)
    parser.add_argument("--snap", action="store_true")
    parser.add_argument("--hold-ms", type=float, default=defaults.hold_ms)
    return parser.parse_args()


def build_detector(name: str, upper_body: bool) -> Detector:
    if name == "pose":
        from adapters.detect_pose import PoseDetector

        return PoseDetector(bust=upper_body)
    return VisionDetector(upper_body=upper_body)


def sha256_16(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def rect_dict(r: Rect | None) -> dict | None:
    return None if r is None else {"x": r.x, "y": r.y, "w": r.w, "h": r.h}


def box_dicts(rects: list[Rect], scores: list[float]) -> list[dict]:
    return [{"x": r.x, "y": r.y, "w": r.w, "h": r.h, "score": s} for r, s in zip(rects, scores)]


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
    return clamp_to_source(fit_ratio(expand(merged, p.margin), p.ratio), p.source_w, p.source_h, p.ratio, p.min_crop_h)


def is_degenerate(merged: Rect | None, target: Rect | None, p: PolicyParams) -> bool:
    """True when the target is pinned rather than chosen: fit_ratio had to
    grow the union (its own aspect was wider than the target ratio) and
    clamp_to_source pinned the result to the source's full height.
    """
    if merged is None or target is None or merged.h <= 0:
        return False
    return (merged.w / merged.h) > p.ratio and target.h == p.source_h


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


def crown_checks(state: PolicyState, now_ms: float, boxes: list[Rect]) -> list[dict]:
    """Crown containment of the rect actually applied this frame -- state.cells
    or state.current, not the freshly computed target the two can diverge
    from for many frames after a commit.

    Split pairing follows state.tracks (sorted by cx, like _cell_rects),
    filtered to a track re-detected this frame; a held/stale track has no
    fresh crown to check.
    """
    if state.mode == "split" and state.cells is not None:
        alive = [t for t in state.tracks if t is not None]
        if len(alive) != 2:
            return []
        top, bottom = sorted(alive, key=lambda t: t.box.cx)
        return [
            _crown_check(track.box.crown, cell.y)
            for track, cell in zip((top, bottom), state.cells)
            if track.last_seen_ms == now_ms and track.box.crown is not None
        ]
    if len(boxes) == 1 and boxes[0].crown is not None:
        return [_crown_check(boxes[0].crown, state.current.y)]
    return []


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def print_summary(
    n_frames: int, n_detections: int, moves: list[dict], detector_name: str, degeneracy: dict, head: dict
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


def main() -> None:
    args = parse_args()
    video = VideoFrames(args.clip, fps=args.fps, width=args.width, start_s=args.start, duration_s=args.duration)
    probe = video.probe()
    detector = build_detector(args.detector, args.upper_body)

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
    )
    state = initial_state(p)

    header = {
        "clip_sha256_16": sha256_16(args.clip),
        "probe": probe,
        "params": {name: getattr(p, name) for name in p.__dataclass_fields__},
        "detector": detector.name,
        "fps": args.fps,
        # probe["fps"] is the source's native rate; fps is what we sample it
        # at, so decimation says how many source frames each sample skips.
        "decimation": probe["fps"] / args.fps,
        "width": args.width,
        "start_s": args.start,
        "duration_s": args.duration,
    }

    n_frames = 0
    n_detections = 0
    moves: list[dict] = []
    degeneracy = {
        "n_degenerate": 0, "n_degenerate_benign": 0, "n_degenerate_harmful": 0,
        "n_multibody": 0, "n_between": 0, "n_on_subject": 0, "n_elsewhere": 0, "n_overlap": 0,
    }
    head: dict = {"n_checked": 0, "n_cut": 0, "n_unreachable": 0, "margins": []}

    with open(args.out, "w") as out:
        out.write(json.dumps(header) + "\n")
        for frame in video:
            boxes = detector.detect(frame.jpeg)
            rects = [to_source_rect(b, p.source_w, p.source_h) for b in boxes]
            box_list = box_dicts(rects, [b.score for b in boxes])
            merged = union(rects)
            target = raw_target(merged, p)
            degenerate = is_degenerate(merged, target, p)

            prev_current = state.current
            state, cmd = step(state, rects, frame.pts_ms, p)
            if cmd is not None:
                move_px = math.hypot(cmd.target.cx - prev_current.cx, cmd.target.cy - prev_current.cy)
                moves.append({"pts_ms": frame.pts_ms, "move_px": move_px})

            placement = crop_vs_subjects(box_list, target.cx) if target is not None else "n/a"
            checks = crown_checks(state, frame.pts_ms, rects)

            entry = {
                "index": frame.index,
                "pts_ms": frame.pts_ms,
                "n_detections": len(boxes),
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
            n_detections += 1 if boxes else 0
            if degenerate:
                degeneracy["n_degenerate"] += 1
                degeneracy["n_degenerate_harmful" if placement == "between" else "n_degenerate_benign"] += 1
            if len(boxes) >= 2:
                degeneracy["n_multibody"] += 1
                degeneracy[_PLACEMENT_KEYS[placement]] += 1
            for check in checks:
                if check["unreachable"]:
                    head["n_unreachable"] += 1
                    continue
                head["n_checked"] += 1
                head["margins"].append(check["margin"])
                head["n_cut"] += 1 if check["cut"] else 0

    print_summary(n_frames, n_detections, moves, detector.name, degeneracy, head)


if __name__ == "__main__":
    main()
