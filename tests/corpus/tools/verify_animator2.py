"""Scratch verification script #2 for the Animator: not part of the repo.

Own (contention-free) instrumentation only: logs what THIS process's
Animator instance decided to send, independent of any external poller.
Covers: per-duration tick count/span/final-exact-match, jump-then-play
ordering, play-supersedes-play, jump-supersedes-play, and tick rate under
artificial contention from a second connection hammering the same OBS.
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


def apply_fn(rect: Rect) -> tuple[dict, dict]:
    return crop_patch(rect, source_w, source_h), overlay_patch(rect, control_scale)


A = Rect(0.0, 0.0, source_w * 0.5, source_h)
B = Rect(source_w * 0.5, 0.0, source_w * 0.5, source_h)


def make_animator() -> tuple[Animator, list[tuple[float, Rect]]]:
    a = Animator(url=URL, password=None, scene=SCENE_NAME, output_id=output_id, frame_id=frame_id)
    log: list[tuple[float, Rect]] = []
    orig_send = Animator._send
    def instrumented(self, rect, fn):
        log.append((time.monotonic(), rect))
        return orig_send(self, rect, fn)
    a._send = instrumented.__get__(a, Animator)
    a.start()
    return a, log


print("=== part 1: jump(A) immediately followed by play(A, B, dur) ===")
print("(reproduces the exact call pattern suspected of racing self._current)\n")
for dur in (200.0, 300.0, 600.0, 900.0):
    a, log = make_animator()
    a.jump(A, apply_fn)          # no sleep: stresses the jump->play race on purpose
    t_start = time.monotonic()
    a.play(A, B, dur, apply_fn)
    while a.busy():
        time.sleep(0.002)
    elapsed = (time.monotonic() - t_start) * 1000
    a.stop()

    play_log = [(t, r) for t, r in log if t >= t_start]
    final_rect = play_log[-1][1] if play_log else None
    exact = final_rect == B
    first_rect = play_log[0][1] if play_log else None
    gaps = [(b - a_) * 1000 for a_, b in zip([t for t, _ in play_log], [t for t, _ in play_log][1:])]
    print(f"dur={dur:.0f}ms: ticks={len(play_log)} span={elapsed:.1f}ms "
          f"final==target:{exact} first.x={first_rect.x if first_rect else None:.1f} "
          f"(A.x={A.x:.1f} B.x={B.x:.1f})")
    if gaps:
        print(f"   inter-tick gap ms: min={min(gaps):.2f} max={max(gaps):.2f} mean={sum(gaps)/len(gaps):.2f} "
              f"-> tick rate ~{1000/(sum(gaps)/len(gaps)):.1f} Hz")

print("\n=== part 2: play() supersedes a running play() ===")
a, log = make_animator()
a.jump(A, apply_fn)
t0 = time.monotonic()
a.play(A, B, 900.0, apply_fn)
time.sleep(0.3)  # mid-flight
mid_rect = a._current
C = Rect(source_w * 0.25, source_h * 0.1, source_w * 0.5, source_h * 0.8)
a.play(A, C, 400.0, apply_fn)  # frm=A passed but should be ignored in favor of current
while a.busy():
    time.sleep(0.002)
a.stop()
post_supersede = [(t, r) for t, r in log if t >= t0 + 0.3]
first_after = post_supersede[0][1] if post_supersede else None
final_after = post_supersede[-1][1] if post_supersede else None
print(f"mid-flight _current.x={mid_rect.x:.1f} (between A.x={A.x:.1f} and B.x={B.x:.1f})")
print(f"first tick after supersede: x={first_after.x:.1f} (should be close to mid_rect.x, not A.x={A.x:.1f})")
print(f"final tick after supersede == C: {final_after == C}")

print("\n=== part 3: jump() supersedes a running play() ===")
a, log = make_animator()
a.jump(A, apply_fn)
t0 = time.monotonic()
a.play(A, B, 900.0, apply_fn)
time.sleep(0.3)
D = Rect(source_w * 0.1, 0.0, source_w * 0.3, source_h * 0.9)
t_jump = time.monotonic()
a.jump(D, apply_fn)
while a.busy():
    time.sleep(0.001)
t_done = time.monotonic()
a.stop()
after_jump = [(t, r) for t, r in log if t >= t_jump]
print(f"ticks after jump(): {len(after_jump)}, first==D exactly: {after_jump[0][1] == D if after_jump else None}, "
      f"latency to apply: {(t_done - t_jump) * 1000:.2f}ms")

print("\n=== part 4: tick rate under artificial contention (second connection @ 75Hz) ===")
stop_load = threading.Event()
def hammer():
    poller = ObsWs(url=URL, password=None)
    poller.connect()
    while not stop_load.is_set():
        try:
            poller.request("GetSceneItemTransform", {"sceneName": SCENE_NAME, "sceneItemId": output_id})
        except Exception:
            pass
        time.sleep(1 / 75.0)
    poller.close()

load_thread = threading.Thread(target=hammer, daemon=True)
load_thread.start()
time.sleep(0.1)

a, log = make_animator()
a.jump(A, apply_fn)
t0 = time.monotonic()
a.play(A, B, 500.0, apply_fn)
while a.busy():
    time.sleep(0.002)
elapsed = (time.monotonic() - t0) * 1000
a.stop()
stop_load.set()
load_thread.join(timeout=1.0)

under_load = [(t, r) for t, r in log if t >= t0]
final_rect = under_load[-1][1] if under_load else None
gaps = [(b - a_) * 1000 for a_, b in zip([t for t, _ in under_load], [t for t, _ in under_load][1:])]
print(f"requested=500ms ticks={len(under_load)} span={elapsed:.1f}ms final==target:{final_rect == B}")
if gaps:
    print(f"inter-tick gap under load ms: min={min(gaps):.2f} max={max(gaps):.2f} mean={sum(gaps)/len(gaps):.2f} "
          f"-> achieved tick rate ~{1000/(sum(gaps)/len(gaps)):.1f} Hz (nominal 60 Hz)")

obs.close()
print("\ndone.")
