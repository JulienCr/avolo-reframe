"""Scene topology for the LSA 2026 WIP vertical reframe.

Counterpart of layout.py for the production-derived collection: the names, uuids
and geometry shared by setup_lsa.py and run.py. Stdlib only.
"""

from dataclasses import dataclass

from scripts.layout import RATIO

COLLECTION_NAME = "LSA 2026 WIP"

VERTICAL_CANVAS_NAME = "Aitum Vertical"
VERTICAL_CANVAS_UUID = "1d7524dd-951e-4b85-b03d-b53c52dd57a6"
VERTICAL_SCENE_NAME = "Vertical Scene"
VERTICAL_SCENE_UUID = "08703c9d-6400-44e1-a1df-00b60f6d47e9"

# Canvas a source-clone's "canvas" setting must name to find a scene of the
# main canvas (e.g. a camera's own "--- CAM *" scene), confirmed against a
# running OBS: GetCanvasList reports this as the main canvas's canvasName.
MAIN_CANVAS_NAME = "Main"
CLONE_TYPE_SCENE = 0

# The full item fills the vertical canvas; a split cell is half of it, so a cell
# ratio of RATIO * 2. Both are checked against RATIO below.
VERTICAL_W, VERTICAL_H = 1080, 1920
CELL_H = VERTICAL_H // 2

DEBUG_SCENE_NAME = "DEBUG - REFRAM"
DEBUG_TILE_W, DEBUG_TILE_H = 960, 540
OVERLAY_PREFIX = "RF Overlay"


@dataclass(frozen=True)
class Camera:
    """One production camera and everything the loop needs to address it."""

    key: str
    label: str
    scene_name: str
    scene_uuid: str
    control_port: int
    debug_x: float
    debug_y: float

    @property
    def overlay_name(self) -> str:
        return f"{OVERLAY_PREFIX} {self.key}"

    @property
    def clone_plain_name(self) -> str:
        """Name of the source-clone driving the full-frame item; SetSceneItemName
        does not exist, so this is the only name OBS ever shows for that item."""
        return f"Cam {self.label} - plain"

    @property
    def clone_split_top_name(self) -> str:
        return f"Cam {self.label} - Split ↑"

    @property
    def clone_split_bottom_name(self) -> str:
        return f"Cam {self.label} - Split ↓"

    @property
    def clone_names(self) -> tuple[str, str, str]:
        """(plain, split top, split bottom): the three clones this camera owns."""
        return self.clone_plain_name, self.clone_split_top_name, self.clone_split_bottom_name


# Main Zoom is a fourth pipeline on the same physical camera as Main, with its
# own punch-in: it gets its own items so the vertical matches what the 16:9 shows.
CAMERAS: dict[str, Camera] = {
    "main": Camera("main", "Main", "--- CAM Main", "f3baafec-e357-41bb-8b28-51dc1d676c50",
                   4466, 0.0, 0.0),
    "mainzoom": Camera("mainzoom", "Main Zoom", "--- CAM Main Zoom", "a9fd8472-bb1e-478f-b801-0cfb94e8a177",
                       4469, 960.0, 0.0),
    "cour": Camera("cour", "Cour", "--- CAM Cour", "0ae610f7-f7ac-4633-be7d-bb7314a7410e",
                   4467, 0.0, 540.0),
    "jardin": Camera("jardin", "Jardin", "--- CAM Jardin", "c5b447b2-64fc-40f3-8a42-5009f7e54f1c",
                     4468, 960.0, 540.0),
}

assert abs(VERTICAL_W / VERTICAL_H - RATIO) < 1e-9
assert abs(VERTICAL_W / CELL_H - RATIO * 2) < 1e-9
