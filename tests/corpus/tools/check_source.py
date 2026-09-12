"""Grabs frames from the OBS media source and checks they are neither black nor frozen."""

import hashlib
import pathlib
import sys
import time

sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.frames import FrameSource
from adapters.obsws import ObsWs
from scripts.layout import CAM_NAME

OUT = pathlib.Path("/private/tmp/claude-502/-Users-julien-cruau-dev2-avolo-reframe/9c462887-07d4-43fe-8133-0004737a3129/scratchpad")

with ObsWs(url="ws://127.0.0.1:4455") as obs:
    frames = FrameSource(obs, CAM_NAME, width=640)
    for i in range(5):
        t0 = time.monotonic()
        data = frames.grab()
        ms = (time.monotonic() - t0) * 1000
        path = OUT / f"frame{i}.jpg"
        path.write_bytes(data)
        print(f"frame{i}: {len(data)} octets, sha1={hashlib.sha1(data).hexdigest()[:12]}, {ms:.0f} ms")
        time.sleep(0.5)
