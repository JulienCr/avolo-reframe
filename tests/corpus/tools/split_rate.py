"""Scratchpad-only analysis: how often the split condition would fire on the
corpus replay JSONL. Reads scripts/corpus.py's own output; does not import or
modify scripts/corpus.py.
"""

import json
import sys

from core.geometry import Rect
from core.policy import PolicyParams, Track, _split_ready

path = sys.argv[1]
p = PolicyParams()

n_frames = 0
n_multibody = 0
n_split_ready = 0

with open(path) as f:
    header = json.loads(f.readline())
    for line in f:
        entry = json.loads(line)
        n_frames += 1
        boxes = entry["boxes"]
        if len(boxes) < 2:
            continue
        n_multibody += 1
        # Mirror _update_tracks' 2-slot cap: take the two highest-score boxes.
        top2 = sorted(boxes, key=lambda b: -b["score"])[:2]
        tracks = [Track(Rect(b["x"], b["y"], b["w"], b["h"]), entry["pts_ms"]) for b in top2]
        if _split_ready(tracks, p):
            n_split_ready += 1

print(f"frames replayed: {n_frames}")
print(f"frames with 2+ bodies: {n_multibody} ({100 * n_multibody / n_frames:.1f}%)")
print(f"of those, split-ready: {n_split_ready} ({100 * n_split_ready / n_multibody:.1f}%)")
print(f"split-ready as fraction of all frames: {100 * n_split_ready / n_frames:.1f}%")
