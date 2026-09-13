"""Extract before/after JPEGs around each candidate cut, via ffmpeg.

Reads candidates.json and pulls one frame at before_ms and one at after_ms
for each candidate, scaled to 640px wide. Regenerates the images/ folder
that was deliberately not committed (regenerable from the extract).
"""
import json
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CUTS_DIR = Path(__file__).resolve().parent
CANDIDATES = CUTS_DIR / "candidates.json"
VIDEO = REPO_ROOT / "tests/fixtures/lab-avolo-58m22-70m00.mp4"
OUT_DIR = CUTS_DIR / "images"


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg introuvable dans le PATH.")
    return ffmpeg


def main():
    if not CANDIDATES.exists():
        raise SystemExit(
            f"Candidats absents : {CANDIDATES}\n"
            "Genere-les d'abord avec : uv run python tests/corpus/cuts/rank_candidates.py"
        )
    if not VIDEO.exists():
        raise SystemExit(
            f"Extrait absent : {VIDEO}\n"
            "Il n'est pas versionne (voir .gitignore) et doit etre fourni separement."
        )

    ffmpeg = find_ffmpeg()
    OUT_DIR.mkdir(exist_ok=True)
    candidates = json.load(open(CANDIDATES))

    for c in candidates:
        tag = int(round(c["pts_ms"]))
        for label, ms in [("before", c["before_ms"]), ("after", c["after_ms"])]:
            out_path = OUT_DIR / f"cut_{tag}_{label}.jpg"
            cmd = [
                ffmpeg, "-y", "-ss", f"{ms / 1000.0:.3f}", "-i", str(VIDEO),
                "-frames:v", "1", "-vf", "scale=640:-1", "-q:v", "3",
                str(out_path), "-loglevel", "error",
            ]
            subprocess.run(cmd, check=True)

    print(f"done: {2 * len(candidates)} images in {OUT_DIR}")


if __name__ == "__main__":
    main()
