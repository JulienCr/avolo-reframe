"""Shared layout constants for the AVOLO Reframe POC scene."""

import sys

COLLECTION_NAME = "AVOLO Reframe"
SCENE_NAME = "AVOLO Reframe POC"
BG_NAME, CAM_NAME, FRAME_NAME = "RF Fond", "RF Cam", "RF Cadre"

CANVAS_W, CANVAS_H = 1920, 1080

CONTROL_X, CONTROL_Y, CONTROL_W, CONTROL_H = 60, 202, 1200, 675
OUTPUT_X, OUTPUT_Y, OUTPUT_W, OUTPUT_H = 1320, 60, 540, 960
RATIO = OUTPUT_W / OUTPUT_H

CAM_KIND = "dshow_input" if sys.platform == "win32" else "macos-avcapture"
MEDIA_KIND = "ffmpeg_source"
