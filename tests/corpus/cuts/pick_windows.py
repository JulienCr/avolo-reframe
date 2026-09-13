"""Pick 40s windows of the extract with the densest concentration of cut-events.

Reads candidates.json (produced by rank_candidates.py) and slides a 40s
window (5s step) over it, ranking windows by candidate count then total
score. Used to choose the reference windows documented in README.md.
"""
import json
from pathlib import Path

CUTS_DIR = Path(__file__).resolve().parent
CANDIDATES = CUTS_DIR / "candidates.json"

WIN_MS = 40000
STEP_MS = 5000
DURATION_MS = 698000


def load_candidates() -> list[dict]:
    if not CANDIDATES.exists():
        raise SystemExit(
            f"Candidats absents : {CANDIDATES}\n"
            "Genere-les d'abord avec : uv run python tests/corpus/cuts/rank_candidates.py"
        )
    return json.load(open(CANDIDATES))


def scan_windows(events: list[dict]) -> list[dict]:
    windows = []
    t = 0
    while t + WIN_MS <= DURATION_MS:
        in_win = [e for e in events if t <= e["pts_ms"] < t + WIN_MS]
        if in_win:
            windows.append({
                "start_ms": t,
                "count": len(in_win),
                "total_score": sum(e["score"] for e in in_win),
            })
        t += STEP_MS
    windows.sort(key=lambda w: (-w["count"], -w["total_score"]))
    return windows


def main():
    events = load_candidates()
    windows = scan_windows(events)
    print("Top windows by candidate count:")
    for w in windows[:15]:
        print(f"  start={w['start_ms']/1000:.0f}s count={w['count']} score={w['total_score']:.1f}")


if __name__ == "__main__":
    main()
