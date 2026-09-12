import sys

sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")

from adapters.obsws import ObsWs
from core.geometry import Rect, default_rect, to_crop
from core.policy import Command, PolicyParams
from scripts.layout import CONTROL_W, SCENE_NAME
from scripts.run import apply_command, find_scene_items, source_size

obs = ObsWs()
obs.connect()
control_id, output_id, frame_id = find_scene_items(obs, SCENE_NAME)
source_w, source_h = source_size(obs, SCENE_NAME, control_id)
scale = CONTROL_W / source_w

calls = []
orig_batch = obs.request_batch


def counting_batch(requests, execution_type="SERIAL_FRAME", halt_on_failure=False):
    calls.append((execution_type, len(requests)))
    return orig_batch(requests, execution_type=execution_type, halt_on_failure=halt_on_failure)


obs.request_batch = counting_batch

p = PolicyParams(source_w=int(source_w), source_h=int(source_h))
start_rect = default_rect(p.source_w, p.source_h, p.ratio)
shifted_rect = Rect(200, 0, start_rect.w, start_rect.h)

instant_cmd = Command(target=shifted_rect, frm=None, duration_ms=0.0, reason="test-instant")
apply_command(obs, instant_cmd, SCENE_NAME, output_id, frame_id, scale, p)

smooth_cmd = Command(target=start_rect, frm=shifted_rect, duration_ms=320.0, reason="test-smooth")
apply_command(obs, smooth_cmd, SCENE_NAME, output_id, frame_id, scale, p)

print("batch calls (execution_type, n_requests):", calls)

negatives = 0
for x in range(0, int(p.source_w), 137):
    for w in (100, 500, 1000, int(p.source_w), int(p.source_w) + 50):
        r = Rect(x, 0, w, p.source_h)
        l, t, rr, b = to_crop(r, p.source_w, p.source_h)
        if min(l, t, rr, b) < 0:
            negatives += 1
            print("NEGATIVE CROP:", r, "->", (l, t, rr, b))
print("negative-crop sweep count:", negatives)

obs.close()
