"""Measures what the real policy targets for two subjects at opposite edges."""

import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from core.geometry import Rect
from core.policy import PolicyParams, _target_from_boxes

p = PolicyParams()
left = Rect(x=270, y=165, w=480, h=825)
right = Rect(x=1290, y=90, w=450, h=900)

for label, boxes in (
    ("les deux sujets", [left, right]),
    ("gauche seul", [left]),
    ("droite seule", [right]),
):
    t = _target_from_boxes(boxes, p)
    print(f"{label:16} -> x={t.x:7.1f} w={t.w:6.1f} h={t.h:6.1f} centre_x={t.cx:7.1f}")

print()
print(f"centre de l'image      = {p.source_w / 2}")
print(f"centre entre les deux  = {(left.cx + right.cx) / 2:.1f}")
