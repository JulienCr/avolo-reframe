"""Paces scene-item transforms over wall-clock time on a background thread.

obs-websocket's SERIAL_FRAME batching does not pace requests to the render
rate (measured: 60 requests land in ~17ms, not ~1s) — so tweening has to be
driven from a real clock instead, without blocking the caller's detect loop.
"""

import threading
import time
from collections.abc import Callable

from adapters.obsws import ObsWs, ObsWsError
from core.geometry import Rect, ease_in_out, lerp_rect

ApplyFn = Callable[[tuple[Rect, ...]], list[tuple[str, dict]]]
_Job = tuple[tuple[Rect, ...], tuple[Rect, ...], float, float, ApplyFn]


class Animator:
    """Tweens a tuple of rects together at a fixed tick rate.

    One rect for a single crop, two for split's stacked cells: apply_fn
    turns the interpolated tuple into the OBS request batch for a tick.

    All socket I/O happens on the background thread started by start():
    play() and jump() only ever touch the job under a lock, so a single
    ObsWs connection is never driven from two threads at once.
    """

    def __init__(self, url: str, password: str | None, hz: float = 60.0) -> None:
        # Own connection: ObsWs's request-id counter and socket are unguarded,
        # so two threads sharing one instance would interleave responses.
        self._obs = ObsWs(url=url, password=password)
        self._period = 1.0 / hz
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._job: _Job | None = None
        self._current: tuple[Rect, ...] | None = None

    def start(self) -> None:
        self._obs.connect()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._obs.close()

    def busy(self) -> bool:
        with self._lock:
            return self._job is not None

    def jump(self, to: tuple[Rect, ...], apply_fn: ApplyFn) -> None:
        """Queue an instant cut: applied whole on the next tick, no tween."""
        with self._lock:
            # Set synchronously: a play() issued right after must see `to`,
            # not the rects from before the jump's own tick has run.
            self._current = to
            self._job = (to, to, 0.0, 0.0, apply_fn)

    def play(self, frm: tuple[Rect, ...], to: tuple[Rect, ...], duration_ms: float, apply_fn: ApplyFn) -> None:
        with self._lock:
            current = self._current
            # Supersede from the current rects, not frm, so motion stays
            # smooth — unless the shape just changed (single vs split).
            start_from = current if current is not None and len(current) == len(to) else frm
            self._job = (start_from, to, time.perf_counter(), max(duration_ms, 1.0), apply_fn)

    def _run(self) -> None:
        next_tick = time.perf_counter()
        while not self._stop.is_set():
            with self._lock:
                job = self._job
            if job is not None:
                self._tick(job)
            next_tick += self._period
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.perf_counter()

    def _tick(self, job: _Job) -> None:
        frm, to, start_time, duration_ms, apply_fn = job
        t = 1.0 if duration_ms <= 0 else (time.perf_counter() - start_time) * 1000.0 / duration_ms
        # Identity, not a t=1.0 lerp: floating-point round-trip through
        # lerp/ease is not guaranteed bit-exact, and the last tick must be.
        if t >= 1.0:
            rects = to
        else:
            eased = ease_in_out(t)
            rects = tuple(lerp_rect(f, s, eased) for f, s in zip(frm, to))
        with self._lock:
            self._current = rects
            if t >= 1.0 and self._job is job:
                self._job = None
        self._send(rects, apply_fn)

    def _send(self, rects: tuple[Rect, ...], apply_fn: ApplyFn) -> None:
        requests = apply_fn(rects)
        try:
            self._obs.ensure_connected()
            self._obs.request_batch(requests, execution_type="SERIAL_REALTIME")
        except ObsWsError:
            pass
