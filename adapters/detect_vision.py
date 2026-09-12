"""Body detection via Apple's Vision framework (macOS only)."""

import Foundation
import Vision

from adapters.detector import Box


class VisionDetector:
    """Detects whole bodies (not faces) using VNDetectHumanRectanglesRequest."""

    def __init__(self, min_score: float = 0.3, upper_body: bool = False) -> None:
        self._min_score = min_score
        self._upper_body = upper_body
        # Upper-body mode is faster and survives an occluded/seated subject,
        # but frames head+torso only: wrong when a whole standing body is wanted.
        self.name = "vision-upper" if upper_body else "vision"

    def detect(self, jpeg: bytes) -> list[Box]:
        nsdata = Foundation.NSData.dataWithBytes_length_(jpeg, len(jpeg))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(nsdata, None)
        request = Vision.VNDetectHumanRectanglesRequest.alloc().init()
        request.setUpperBodyOnly_(self._upper_body)

        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Vision request failed: {error}")

        boxes = []
        for observation in request.results() or []:
            score = float(observation.confidence())
            if score < self._min_score:
                continue
            (x, y, w, h) = self._to_top_left(observation.boundingBox())
            boxes.append(Box(x=x, y=y, w=w, h=h, score=score))
        return boxes

    @staticmethod
    def _to_top_left(bounding_box) -> tuple[float, float, float, float]:
        (origin_x, origin_y), (width, height) = bounding_box
        # Vision's normalized space is bottom-left origin, y up; ours is top-left, y down.
        y_top = 1.0 - (origin_y + height)
        values = (origin_x, y_top, width, height)
        return tuple(min(1.0, max(0.0, v)) for v in values)
