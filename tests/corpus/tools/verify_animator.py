"""Scratch verification script for the Animator: not part of the repo.

Proves (1) tick pacing and (2) progressive crop change on the live OBS scene.
"""

import sys
import threading
import time

sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")

from adapters.animator import Animator
from adapters.obsws import ObsWs
from core.geometry import Rect
from scripts.layout import CONTROL_W, SCENE_NAME
from scripts.run import crop_patch, find_scene_items, overlay_patch, source_size

URL = "ws://127.0.0.1:4455"

obs = ObsWs(url=URL, password=None)
obs.connect()
control_id, output_id, frame_id = find_scene_items(obs, SCENE_NAME)
source_w, source_h = source_size(obs, SCENE_NAME, control_id)
control_scale = CONTROL_W / source_w
print(f"source={source_w}x{source_h} output_id={output_id} frame_id={frame_id}")

def apply_fn(rect: Rect) -> tuple[dict, dict]:
    return crop_patch(rect, source_w, source_h), overlay_patch(rect, control_scale)

# Instrument _send to record tick timestamps and the intended rect,
# without editing the module file. This is contamination-free: it reads
# what THIS Animator instance decided to send, not shared OBS state.
sent_times: list[float] = []
sent_rects: list[Rect] = []
orig_send = Animator._send
def instrumented(self, rect, apply_fn):
    sent_times.append(time.monotonic())
    sent_rects.append(rect)
    return orig_send(self, rect, apply_fn)
Animator._send = instrumented

animator = Animator(url=URL, password=None, scene=SCENE_NAME, output_id=output_id, frame_id=frame_id)
animator.start()

frm = Rect(0.0, 0.0, source_w * 0.5, source_h)
to = Rect(source_w * 0.5, 0.0, source_w * 0.5, source_h)
animator.jump(frm, apply_fn)
time.sleep(0.2)

# Separate poller connection/process-equivalent: independent ObsWs, samples
# GetSceneItemTransform on the output item during the transition.
poll_samples: list[tuple[float, float]] = []
stop_poll = threading.Event()

def poll():
    poller = ObsWs(url=URL, password=None)
    poller.connect()
    t_start = time.monotonic()
    while not stop_poll.is_set():
        t = poller.request("GetSceneItemTransform", {"sceneName": SCENE_NAME, "sceneItemId": output_id})[
            "sceneItemTransform"
        ]
        poll_samples.append((time.monotonic() - t_start, t["cropLeft"]))
        time.sleep(0.02)
    poller.close()

poll_thread = threading.Thread(target=poll, daemon=True)
poll_thread.start()

DURATION_MS = 500.0
t_play_start = time.monotonic()
animator.play(frm, to, DURATION_MS, apply_fn)
while animator.busy():
    time.sleep(0.005)
elapsed = time.monotonic() - t_play_start

stop_poll.set()
poll_thread.join(timeout=1.0)

print(f"\n--- item 1: tick pacing for a {DURATION_MS:.0f} ms transition (own log, contamination-free) ---")
# Only ticks inside the play() window: the pre-play jump() tick and the
# idle gap before play() would otherwise pollute the inter-tick stats.
window_idx = [i for i, t in enumerate(sent_times) if t_play_start <= t <= t_play_start + elapsed + 0.05]
window = [sent_times[i] for i in window_idx]
print(f"ticks sent in window: {len(window)}, wall-clock span: {elapsed * 1000:.1f} ms")
if len(window) >= 2:
    gaps = [(b - a) * 1000 for a, b in zip(window, window[1:])]
    print(f"inter-tick gap: min={min(gaps):.2f}ms max={max(gaps):.2f}ms mean={sum(gaps) / len(gaps):.2f}ms")
print("rect.x progression (own log):", [round(sent_rects[i].x) for i in window_idx])

print(f"\n--- item 2: progressive crop change polled via a separate connection, every ~20ms ---")
print("(shared OBS instance: other fleet agents may write to the same scene concurrently)")
for t_rel, crop_left in poll_samples:
    print(f"t={t_rel * 1000:6.1f}ms cropLeft={crop_left:.1f}")

animator.stop()
obs.close()
print("\nanimator stopped.")
