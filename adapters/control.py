"""HTTP control server for the AVOLO Reframe dock.

One ControlState instance is written each iteration by the live loop and
read by the HTTP handlers below; a single lock guards both sides.
"""

import dataclasses
import json
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.policy import PolicyParams

_WINDOW = 200

_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "margin": (0.0, 5.0),
    "min_crop_h": (1.0, 1080.0),  # 1080p pixels, scaled by core.policy.height_floor to the actual source
    "dead_zone": (0.0, 1.0),
    "dwell_ms": (0.0, 20000.0),
    "ease_ms": (0.0, 20000.0),
    "hold_ms": (0.0, 60000.0),
}
_FPS_BOUNDS = (0.1, 60.0)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class ControlState:
    """Shared snapshot: the loop calls publish()/set_*(), the server reads snapshot()."""

    def __init__(self, params: PolicyParams, detector_name: str, fps: float, upper_body: bool) -> None:
        self.lock = threading.Lock()
        self.params = params
        self.detector_name = detector_name
        self.fps = fps
        self.upper_body = upper_body
        self.paused = False
        self.live = True
        self.connected = True
        self.crop = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self.mode = "single"
        self.cells: list[dict] | None = None
        self.boxes: list[dict] = []
        self.frame_jpeg: bytes | None = None
        self.features: dict | None = None
        self.commands_emitted = 0
        self.stats: dict[str, deque] = {
            k: deque(maxlen=_WINDOW) for k in ("capture", "detect", "policy", "emit", "features")
        }
        self.detections: deque = deque(maxlen=_WINDOW)
        self._iter_times: deque = deque(maxlen=_WINDOW)
        # "recenter" is one-shot and must fire exactly once; pause/resume are
        # level state instead, so they are plain flags rather than queued.
        self.pending_actions: list[str] = []

    def publish(
        self,
        frame_jpeg: bytes,
        boxes: list[dict],
        crop: dict,
        stage_ms: dict[str, float],
        emitted: bool,
        mode: str = "single",
        cells: list[dict] | None = None,
        features: dict | None = None,
    ) -> None:
        with self.lock:
            self.frame_jpeg = frame_jpeg
            self.boxes = boxes
            self.crop = crop
            self.mode = mode
            self.cells = cells
            self._iter_times.append(time.perf_counter())
            self.detections.append(bool(boxes))
            for stage, ms in stage_ms.items():
                self.stats[stage].append(ms)
            if emitted:
                self.commands_emitted += 1
            if features is not None:
                self.features = features

    def set_detector_name(self, name: str) -> None:
        with self.lock:
            self.detector_name = name

    def set_connected(self, connected: bool) -> None:
        with self.lock:
            self.connected = connected

    def replace_source_size(self, source_w: int, source_h: int) -> None:
        """Update just the source geometry, keeping any dock-tuned fields;
        called when the live loop detects the source has changed size.
        """
        with self.lock:
            self.params = dataclasses.replace(self.params, source_w=source_w, source_h=source_h)

    def get_controls(self) -> tuple[PolicyParams, float, bool, bool]:
        """Current (params, fps, upper_body, paused), as last requested by the dock."""
        with self.lock:
            return self.params, self.fps, self.upper_body, self.paused

    def set_live(self, live: bool) -> None:
        """Mirror the loop's own on-air state, so the dock shows what it does."""
        with self.lock:
            self.live = live

    def pop_action(self) -> str | None:
        with self.lock:
            return self.pending_actions.pop(0) if self.pending_actions else None

    def apply_updates(self, policy_updates: dict, fps: float | None, upper_body: bool | None) -> None:
        with self.lock:
            if policy_updates:
                self.params = dataclasses.replace(self.params, **policy_updates)
            if fps is not None:
                self.fps = fps
            if upper_body is not None:
                self.upper_body = upper_body

    def request_action(self, action: str) -> None:
        with self.lock:
            if action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused = False
            else:
                self.pending_actions.append(action)

    def snapshot(self) -> dict:
        with self.lock:
            rate = 0.0
            span = self._iter_times[-1] - self._iter_times[0] if len(self._iter_times) >= 2 else 0.0
            # Not monotonic(): on Windows with Python 3.12 it steps by 15.6 ms, so two
            # iterations could share a timestamp and divide by zero.
            if span > 0:
                rate = (len(self._iter_times) - 1) / span
            detection_rate = sum(self.detections) / len(self.detections) if self.detections else 0.0
            stats = {
                stage: {"median": _percentile(list(vals), 0.5), "p90": _percentile(list(vals), 0.9)}
                for stage, vals in self.stats.items()
            }
            return {
                "params": dataclasses.asdict(self.params),
                "detector": self.detector_name,
                "fps": self.fps,
                "upper_body": self.upper_body,
                "source_w": self.params.source_w,
                "source_h": self.params.source_h,
                "iterations_per_s": rate,
                "detection_rate": detection_rate,
                "crop": self.crop,
                "mode": self.mode,
                "cells": self.cells,
                "boxes": self.boxes,
                "commands_emitted": self.commands_emitted,
                "stats": stats,
                "connected": self.connected,
                "paused": self.paused,
                "live": self.live,
            }


class ControlHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass  # the default access log would drown the loop's own stdout

    def do_GET(self) -> None:
        if self.path in ("/", "/dock.html"):
            self._send_html(self.server.dock_html)
        elif self.path == "/overlay.html":
            self._send_html(self.server.overlay_html)
        elif self.path == "/api/state":
            self._send_json(self.server.state.snapshot())
        elif self.path == "/api/frame.jpg":
            self._send_frame()
        elif self.path == "/api/features":
            self._send_features()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/api/params":
            self._handle_params()
        elif self.path == "/api/action":
            self._handle_action()
        else:
            self.send_error(404)

    def _handle_params(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return

        policy_updates: dict = {}
        fps_update: float | None = None
        upper_body_update: bool | None = None
        for key, value in payload.items():
            if key in _PARAM_BOUNDS:
                lo, hi = _PARAM_BOUNDS[key]
                if not _is_number(value) or not (lo <= value <= hi):
                    self._send_error_json(400, f"{key} must be a number in [{lo}, {hi}]")
                    return
                policy_updates[key] = float(value)
            elif key == "snap":
                if not isinstance(value, bool):
                    self._send_error_json(400, "snap must be a boolean")
                    return
                policy_updates["snap"] = value
            elif key == "upper_body":
                if not isinstance(value, bool):
                    self._send_error_json(400, "upper_body must be a boolean")
                    return
                upper_body_update = value
            elif key == "fps":
                lo, hi = _FPS_BOUNDS
                if not _is_number(value) or not (lo <= value <= hi):
                    self._send_error_json(400, f"fps must be a number in [{lo}, {hi}]")
                    return
                fps_update = float(value)
            else:
                self._send_error_json(400, f"unknown or locked field: {key}")
                return

        self.server.state.apply_updates(policy_updates, fps_update, upper_body_update)
        self._send_json(self.server.state.snapshot())

    def _handle_action(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        action = payload.get("action")
        if action not in ("pause", "resume", "recenter", "live", "no-live"):
            self._send_error_json(400, "action must be pause, resume, recenter, live or no-live")
            return
        self.server.state.request_action(action)
        self._send_json(self.server.state.snapshot())

    def _send_frame(self) -> None:
        with self.server.state.lock:
            jpeg = self.server.state.frame_jpeg
        if jpeg is None:
            self._send_error_json(503, "no frame yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(jpeg)

    def _send_features(self) -> None:
        with self.server.state.lock:
            features = self.server.state.features
        if features is None:
            self._send_error_json(503, "no features yet")
            return
        self._send_json(features)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_html(self, html: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            raise ValueError("empty request body")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True  # let Ctrl-C exit even with a request in flight
    if sys.platform == "win32":
        # Windows SO_REUSEADDR lets a second process silently rebind the same
        # port instead of failing, unlike on POSIX: refuse that here.
        allow_reuse_address = False

    def __init__(self, state: ControlState, dock_html: bytes, overlay_html: bytes, port: int) -> None:
        super().__init__(("127.0.0.1", port), ControlHandler)
        self.state = state
        self.dock_html = dock_html
        self.overlay_html = overlay_html


def start_server(state: ControlState, port: int, dock_path: Path, overlay_path: Path) -> ControlServer:
    """Serve the dock, the OBS overlay and the control API on a daemon thread; localhost only."""
    server = ControlServer(state, dock_path.read_bytes(), overlay_path.read_bytes(), port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
