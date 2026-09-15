"""Body-pose detection via YOLO11-pose (Windows, CUDA required)."""

import io
import os
from pathlib import Path

os.environ.setdefault("YOLO_AUTOINSTALL", "false")

import torch
from PIL import Image
from ultralytics import YOLO

from adapters.detector import Box
from adapters.pose_geometry import Pose, coco17_to_joints, detect_poses

# Vision's default; kept identical so both detectors apply the same crown/bust rules.
_MIN_JOINT_CONFIDENCE = 0.15
_PERSON_CLASS = 0


class YoloDetector:
    """Detects body poses via ultralytics YOLO11-pose, GPU only."""

    def __init__(self, model_path: str, bust: bool = False, imgsz: int = 640, conf: float = 0.25) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA indisponible pour le détecteur yolo. Si torch est installé mais ne voit pas le GPU, "
                "c'est probablement la variante CPU (roue par défaut de PyPI) : réinstallez depuis "
                "l'index cu128 (voir pyproject.toml)."
            )
        self.model_path = model_path
        self.imgsz = imgsz
        self.conf = conf
        self._bust = bust
        self._model = YOLO(model_path)
        engine = "-trt" if Path(model_path).suffix == ".engine" else ""
        self.name = f"yolo-{Path(model_path).stem}{engine}" + ("-bust" if bust else "")
        warmup_result = self._predict(Image.new("RGB", (640, 360)))
        if warmup_result.keypoints is None:
            raise RuntimeError(
                f"« {model_path} » n'est pas un modèle de pose (pas de keypoints) : "
                "utilisez un poids *-pose (ex. yolo11m-pose.pt)."
            )

    def detect(self, jpeg: bytes) -> list[Box]:
        return [pose.box for pose in self.detect_poses(jpeg)]

    def detect_poses(self, jpeg: bytes) -> list[Pose]:
        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
        width, height = image.size
        result = self._predict(image)

        keypoints = result.keypoints.data.tolist()
        all_joints = [coco17_to_joints(keypoints[i], width, height) for i in range(len(result.boxes))]
        return detect_poses(all_joints, _MIN_JOINT_CONFIDENCE, self._bust)

    def _predict(self, image: Image.Image):
        results = self._model.predict(
            image, imgsz=self.imgsz, conf=self.conf, classes=[_PERSON_CLASS], quantize=16, verbose=False, device=0
        )
        return results[0]
