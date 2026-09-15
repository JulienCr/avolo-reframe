"""Latency bench: .pt vs .engine YOLO11-pose, same process, same 39 corpus frames.

Run as: uv run python tests/corpus/tools/bench_detectors.py [model_stem]
"""

import glob
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PIL import Image

from adapters.detect_yolo import YoloDetector

_FRAMES_DIR = Path(__file__).resolve().parents[1] / "frames"
_MODEL_DIR = Path(__file__).resolve().parents[3] / "models"
_WIDTH = 640
_JPEG_QUALITY = 75
_ROUNDS = 6


def _resized_jpeg(path: Path) -> bytes:
    """Mimic the loop's frame path: decode, scale to _WIDTH wide, re-encode as JPEG."""
    image = Image.open(path).convert("RGB")
    w, h = image.size
    new_h = round(h * _WIDTH / w / 2) * 2
    image = image.resize((_WIDTH, new_h))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def main() -> None:
    stem = sys.argv[1] if len(sys.argv) > 1 else "yolo11m-pose"
    frames = [_resized_jpeg(Path(p)) for p in sorted(glob.glob(str(_FRAMES_DIR / "*.jpg")))]
    print(f"{len(frames)} images, {_WIDTH}px de large, qualité JPEG {_JPEG_QUALITY}.")

    detectors = {"pt": YoloDetector(str(_MODEL_DIR / f"{stem}.pt"))}
    engine_path = _MODEL_DIR / f"{stem}.engine"
    if engine_path.is_file():
        detectors["engine"] = YoloDetector(str(engine_path))
    else:
        print(f"Pas de moteur TensorRT à {engine_path} : comparaison .pt seul.")

    timings: dict[str, list[float]] = {name: [] for name in detectors}
    box_counts: dict[str, list[int]] = {name: [] for name in detectors}

    for round_index in range(_ROUNDS):
        for jpeg in frames:
            for name, detector in detectors.items():
                t0 = time.perf_counter()
                boxes = detector.detect(jpeg)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                # The first round pays cold-start cost (kernel autotune, page faults):
                # discarded here, not just noted, per this repo's own measurement rule.
                if round_index > 0:
                    timings[name].append(elapsed_ms)
                    box_counts[name].append(len(boxes))

    print(f"\n{_ROUNDS - 1} rounds mesurés (1er rejeté).")
    for name in detectors:
        values = timings[name]
        counts = box_counts[name]
        print(
            f"  {name}: médiane={percentile(values, 0.5):.2f}ms p95={percentile(values, 0.95):.2f}ms "
            f"boîtes/image médiane={percentile([float(c) for c in counts], 0.5):.0f}"
        )


if __name__ == "__main__":
    main()
