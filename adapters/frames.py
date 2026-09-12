"""Frame source: grabs JPEG screenshots from an OBS source over obs-websocket."""

import base64

from adapters.obsws import ObsWs


class FrameSource:
    """Pulls a single-frame JPEG screenshot from a named OBS source."""

    def __init__(self, obs: ObsWs, source_name: str, width: int = 640, quality: int = 75) -> None:
        self._obs = obs
        self._source_name = source_name
        self._width = width
        self._quality = quality

    def grab(self) -> bytes:
        response = self._obs.request(
            "GetSourceScreenshot",
            {
                "sourceName": self._source_name,
                "imageFormat": "jpg",
                "imageWidth": self._width,
                "imageCompressionQuality": self._quality,
            },
        )
        image_data = response["imageData"]
        # OBS returns a data URI ("data:image/jpg;base64,...."), not bare base64.
        _, _, payload = image_data.partition(",")
        return base64.b64decode(payload if payload else image_data)
