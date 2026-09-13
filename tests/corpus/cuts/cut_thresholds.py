"""Compute the frame-to-frame jump distribution used to pick cut-detection thresholds.

Reads the full reference trace and prints percentiles of the center-jump
(pixels) and scale-jump (|log ratio|) signals between consecutive frames.
Run this first when re-tuning CENTER_THRESHOLD / SCALE_THRESHOLD in
detect_cuts.py — the values there were picked from this output.
"""
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TRACE = REPO_ROOT / "tests/corpus/traces/full-reference-pose.jsonl"


def load_frames(trace_path: Path) -> list[dict]:
    if not trace_path.exists():
        raise SystemExit(
            f"Trace absente : {trace_path}\n"
            "Elle n'est pas versionnee (voir .gitignore) et doit etre regeneree :\n"
            "  uv run python -m scripts.corpus tests/fixtures/lab-avolo-58m22-70m00.mp4 \\\n"
            "      --detector pose --fps 12 --out tests/corpus/traces/full-reference-pose.jsonl\n"
            "Necessite aussi tests/fixtures/lab-avolo-58m22-70m00.mp4, egalement ignore par git."
        )
    with open(trace_path) as f:
        lines = f.readlines()
    return [json.loads(l) for l in lines[1:]]


def center(u):
    return (u["x"] + u["w"] / 2, u["y"] + u["h"] / 2)


def diag(u):
    return math.hypot(u["w"], u["h"])


def pct(sorted_list, p):
    k = min(int(len(sorted_list) * p), len(sorted_list) - 1)
    return sorted_list[k]


def main():
    frames = load_frames(TRACE)
    print(f"n_frames: {len(frames)}")

    center_jumps = []
    scale_jumps = []
    for i in range(1, len(frames)):
        ua, ub = frames[i - 1]["union"], frames[i]["union"]
        if ua is None or ub is None:
            continue
        ca, cb = center(ua), center(ub)
        center_jumps.append(math.hypot(cb[0] - ca[0], cb[1] - ca[1]))
        da, db = diag(ua), diag(ub)
        if da > 0 and db > 0:
            scale_jumps.append(abs(math.log(db / da)))

    center_jumps.sort()
    scale_jumps.sort()

    print("center_jump distribution (px):")
    for p in [0.5, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999]:
        print(f"  p{p}: {pct(center_jumps, p):.1f}")
    print(f"  max: {center_jumps[-1]:.1f}")

    print("scale_jump distribution (|log ratio|):")
    for p in [0.5, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999]:
        print(f"  p{p}: {pct(scale_jumps, p):.4f}")
    print(f"  max: {scale_jumps[-1]:.4f}")


if __name__ == "__main__":
    main()
