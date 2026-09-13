"""Flag frame-to-frame discontinuities in the reference trace (candidate cuts).

Standalone diagnostic pass: for each consecutive frame pair, scores three
signals (center jump, scale jump, persistent detection-count change, union
appear/disappear) against the thresholds picked in cut_thresholds.py, and
prints the top 40 by raw score. This is the detection step only — it does
not dedupe near-duplicate flags or cap them per time window; that is
rank_candidates.py's job, which is what actually produces candidates.json.
"""
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TRACE = REPO_ROOT / "tests/corpus/traces/full-reference-pose.jsonl"

# Picked from the empirical distribution in cut_thresholds.py: p98 ~= 121px /
# 0.19, p99 ~= 173px / 0.36 -> retain above p98, below the p99.5 tail.
CENTER_THRESHOLD = 150.0
SCALE_THRESHOLD = 0.28


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


def flag_transitions(frames: list[dict]) -> list[dict]:
    candidates = []
    n = len(frames)
    for i in range(1, n):
        a, b = frames[i - 1], frames[i]
        ua, ub = a["union"], b["union"]
        ndet_a, ndet_b = a["n_detections"], b["n_detections"]

        signals = []
        score = 0.0
        center_jump = None
        scale_jump = None

        if ua is not None and ub is not None:
            ca, cb = center(ua), center(ub)
            center_jump = math.hypot(cb[0] - ca[0], cb[1] - ca[1])
            da, db = diag(ua), diag(ub)
            scale_ratio = db / da if da > 0 else None
            scale_jump = abs(math.log(scale_ratio)) if scale_ratio and scale_ratio > 0 else None

            if center_jump > CENTER_THRESHOLD:
                signals.append("center_jump")
                score += center_jump / CENTER_THRESHOLD
            if scale_jump is not None and scale_jump > SCALE_THRESHOLD:
                signals.append("scale_jump")
                score += scale_jump / SCALE_THRESHOLD
        else:
            signals.append("union_appear_disappear")
            score += 1.0

        # Persistent n_detections change: confirm the new count holds for the
        # next frame too (and the old one held for the previous frame), to
        # filter a single-frame detector blink.
        ndet_delta = ndet_b - ndet_a
        if ndet_delta != 0:
            prev_ok = i < 2 or frames[i - 2]["n_detections"] == ndet_a
            next_ok = (i + 1 >= n) or frames[i + 1]["n_detections"] == ndet_b
            if prev_ok and next_ok:
                signals.append("ndet_change_persistent")
                score += abs(ndet_delta) * 0.8
            else:
                signals.append("ndet_change_blink")
                score += abs(ndet_delta) * 0.15

        if not signals:
            continue

        candidates.append({
            "pts_ms": b["pts_ms"],
            "index": b["index"],
            "signals": signals,
            "center_jump_px": round(center_jump, 1) if center_jump is not None else None,
            "scale_jump_log": round(scale_jump, 4) if scale_jump is not None else None,
            "ndet_before": ndet_a,
            "ndet_after": ndet_b,
            "score": round(score, 3),
        })
    return candidates


def main():
    frames = load_frames(TRACE)
    candidates = flag_transitions(frames)
    print(f"total flagged (any signal): {len(candidates)}")

    top = sorted(candidates, key=lambda c: -c["score"])[:40]
    top.sort(key=lambda c: c["pts_ms"])
    print(f"top by raw score (no bucket cap): {len(top)}")
    for c in top:
        print(c["pts_ms"], c["signals"], c["score"])


if __name__ == "__main__":
    main()
