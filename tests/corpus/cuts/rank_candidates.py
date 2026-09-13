"""Score, dedupe and cap cut candidates, producing candidates.json and events.json.

Recomputes the same per-transition signals as detect_cuts.py directly from
the trace (not chained from its output, to stay a single self-contained
pass), then merges transitions within 300ms of each other into one cut-event
(a single cut is often flagged by both the center-jump and the
ndet_change signals on the same pair of frames), and caps candidates per
40s bucket so one densely-cut region doesn't crowd out the rest of the
extract for a human validator.
"""
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TRACE = REPO_ROOT / "tests/corpus/traces/full-reference-pose.jsonl"
OUT_DIR = Path(__file__).resolve().parent
OUT_CANDIDATES = OUT_DIR / "candidates.json"
OUT_EVENTS = OUT_DIR / "events.json"

CENTER_THRESHOLD = 150.0
SCALE_THRESHOLD = 0.28
MERGE_WINDOW_MS = 300
BUCKET_MS = 40000
MAX_PER_BUCKET = 4
TOP_N = 40


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


def build_events(frames: list[dict]) -> list[dict]:
    n = len(frames)
    events = []
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
            sr = db / da if da > 0 else None
            scale_jump = abs(math.log(sr)) if sr and sr > 0 else None
            if center_jump > CENTER_THRESHOLD:
                signals.append("center_jump")
                score += center_jump / CENTER_THRESHOLD
            if scale_jump is not None and scale_jump > SCALE_THRESHOLD:
                signals.append("scale_jump")
                score += scale_jump / SCALE_THRESHOLD
        else:
            signals.append("union_appear_disappear")
            score += 1.0
        if ndet_b != ndet_a:
            prev_ok = i < 2 or frames[i - 2]["n_detections"] == ndet_a
            next_ok = (i + 1 >= n) or frames[i + 1]["n_detections"] == ndet_b
            if prev_ok and next_ok:
                signals.append("ndet_change_persistent")
                score += abs(ndet_b - ndet_a) * 0.8
        if signals:
            events.append({
                "pts_ms": b["pts_ms"],
                "index_before": a["index"],
                "index_after": b["index"],
                "signals": signals,
                "center_jump_px": round(center_jump, 1) if center_jump is not None else None,
                "scale_jump_log": round(scale_jump, 4) if scale_jump is not None else None,
                "ndet_before": ndet_a,
                "ndet_after": ndet_b,
                "score": round(score, 3),
            })
    return events


def merge_close(events: list[dict]) -> list[dict]:
    events = sorted(events, key=lambda e: e["pts_ms"])
    merged = []
    for e in events:
        if merged and e["pts_ms"] - merged[-1]["pts_ms"] < MERGE_WINDOW_MS:
            if e["score"] > merged[-1]["score"]:
                merged[-1] = e
        else:
            merged.append(e)
    return merged


def cap_per_bucket(merged: list[dict]) -> list[dict]:
    # Sorts merged in place by score descending: this is also the order the
    # surensemble is written in (events.json), since capping picks greedily
    # off this same order rather than a separate copy.
    merged.sort(key=lambda e: -e["score"])
    per_bucket_count = {}
    capped, overflow = [], []
    for e in merged:
        bucket = int(e["pts_ms"] // BUCKET_MS)
        if per_bucket_count.get(bucket, 0) < MAX_PER_BUCKET:
            capped.append(e)
            per_bucket_count[bucket] = per_bucket_count.get(bucket, 0) + 1
        else:
            overflow.append(e)
    top = capped[:TOP_N]
    if len(top) < TOP_N:
        top += overflow[: TOP_N - len(top)]
    top.sort(key=lambda e: e["pts_ms"])
    return top


def main():
    frames = load_frames(TRACE)
    events = build_events(frames)
    merged = merge_close(events)
    print(f"raw transitions flagged: {len(events)} -> deduped cut-events: {len(merged)}")

    # top shares dict objects with merged: setting "confidence" below mutates
    # the same entries, so they also carry it in events.json.
    top = cap_per_bucket(merged)
    for c in top:
        c["confidence"] = "haute" if c["score"] >= 5 else ("moyenne" if c["score"] >= 2 else "basse")

    # before_ms/after_ms are added on copies so they land in candidates.json
    # only, not on the shared objects dumped to events.json.
    candidates_out = [
        dict(c, before_ms=frames[c["index_before"]]["pts_ms"], after_ms=c["pts_ms"])
        for c in top
    ]

    with open(OUT_CANDIDATES, "w") as f:
        json.dump(candidates_out, f, indent=2, ensure_ascii=False)
    with open(OUT_EVENTS, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"kept {len(top)} candidates -> {OUT_CANDIDATES}")
    for c in candidates_out:
        print(f"  {c['pts_ms']:.0f}ms score={c['score']:.2f} {c['signals']}")


if __name__ == "__main__":
    main()
