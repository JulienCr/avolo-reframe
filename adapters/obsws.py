"""Minimal synchronous obs-websocket v5 client for a single-threaded PoC loop.

Synchronous by design: the caller is a capture/detect/emit loop, not an
event loop, so this module never touches asyncio.
"""

import base64
import hashlib
import itertools
import json
import time

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

_OP_HELLO = 0
_OP_IDENTIFY = 1
_OP_IDENTIFIED = 2
_OP_REQUEST = 6
_OP_REQUEST_RESPONSE = 7
_OP_REQUEST_BATCH = 8
_OP_REQUEST_BATCH_RESPONSE = 9

_EXECUTION_TYPES = {
    "NONE": -1,
    "SERIAL_REALTIME": 0,
    "SERIAL_FRAME": 1,
    "PARALLEL": 2,
}

_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 10.0
_BACKOFF_ATTEMPTS = 6


# Requests that target a scene take either form; a non-main canvas is only
# addressable by uuid, so call sites pass the ref through instead of a name.
SceneRef = dict[str, str]


class ObsWsError(Exception):
    """Raised on connection failure, timeout, or a request whose status.result is false."""


class ObsWs:
    """Synchronous obs-websocket v5 (rpcVersion 1) client."""

    def __init__(
        self,
        url: str = "ws://127.0.0.1:4455",
        password: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._url = url
        self._password = password
        self._timeout = timeout
        self._ws: ClientConnection | None = None
        self._ids = itertools.count(1)

    def __enter__(self) -> "ObsWs":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        if self._ws is not None:
            return
        try:
            ws = connect(self._url, open_timeout=self._timeout)
        except Exception as exc:
            raise ObsWsError(f"failed to connect to {self._url}: {exc}") from exc
        try:
            self._handshake(ws)
        except Exception:
            ws.close()
            raise
        self._ws = ws

    def close(self) -> None:
        if self._ws is None:
            return
        try:
            self._ws.close()
        except Exception:
            pass
        self._ws = None

    def ensure_connected(self) -> None:
        """Reconnect with exponential backoff; give up after 6 attempts."""
        if self._ws is not None:
            return
        delay = _BACKOFF_BASE
        last_error: Exception | None = None
        for _ in range(_BACKOFF_ATTEMPTS):
            try:
                self.connect()
                return
            except ObsWsError as exc:
                last_error = exc
                time.sleep(delay)
                delay = min(delay * 2, _BACKOFF_CAP)
        raise ObsWsError(f"could not reconnect to {self._url}: {last_error}")

    def request(self, request_type: str, data: dict | None = None, timeout: float | None = None) -> dict:
        ws = self._require_connected()
        request_id = str(next(self._ids))
        d = {"requestType": request_type, "requestId": request_id, "requestData": data or {}}
        ws.send(json.dumps({"op": _OP_REQUEST, "d": d}))
        resp = self._await(_OP_REQUEST_RESPONSE, lambda d: d.get("requestId") == request_id, timeout=timeout)
        return self._unwrap(resp)

    def request_batch(
        self,
        requests: list[tuple[str, dict]],
        execution_type: str = "SERIAL_FRAME",
        halt_on_failure: bool = False,
    ) -> list[dict]:
        ws = self._require_connected()
        batch_id = str(next(self._ids))
        # Sub-request IDs must be unique too; they are how results are matched below.
        wire = [
            {"requestType": rt, "requestId": str(next(self._ids)), "requestData": rd}
            for rt, rd in requests
        ]
        d = {
            "requestId": batch_id,
            "haltOnFailure": halt_on_failure,
            "executionType": _EXECUTION_TYPES[execution_type],
            "requests": wire,
        }
        ws.send(json.dumps({"op": _OP_REQUEST_BATCH, "d": d}))
        resp = self._await(_OP_REQUEST_BATCH_RESPONSE, lambda d: d.get("requestId") == batch_id)
        by_id = {r["requestId"]: r for r in resp["results"]}
        # Reorder by our own IDs rather than trusting result list order.
        return [self._unwrap(by_id[w["requestId"]]) for w in wire]

    def _require_connected(self) -> ClientConnection:
        if self._ws is None:
            raise ObsWsError("not connected: call connect() or ensure_connected() first")
        return self._ws

    @staticmethod
    def _unwrap(d: dict) -> dict:
        status = d["requestStatus"]
        if not status["result"]:
            raise ObsWsError(
                f"{d['requestType']} failed: code={status['code']} comment={status.get('comment')}"
            )
        return d.get("responseData", {})

    def _handshake(self, ws: ClientConnection) -> None:
        hello = self._recv_op(ws, _OP_HELLO, lambda d: True)
        identify = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello.get("authentication")
        if auth:
            identify["authentication"] = self._build_auth(auth["challenge"], auth["salt"])
        ws.send(json.dumps({"op": _OP_IDENTIFY, "d": identify}))
        self._recv_op(ws, _OP_IDENTIFIED, lambda d: True)

    def _build_auth(self, challenge: str, salt: str) -> str:
        if self._password is None:
            raise ObsWsError("OBS requires authentication but no password was configured")
        secret = self._b64_sha256(self._password + salt)
        return self._b64_sha256(secret + challenge)

    @staticmethod
    def _b64_sha256(text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _await(self, op: int, matches, timeout: float | None = None) -> dict:
        return self._recv_op(self._require_connected(), op, matches, timeout=timeout)

    def _recv_op(self, ws: ClientConnection, op: int, matches, timeout: float | None = None) -> dict:
        deadline = time.perf_counter() + (timeout if timeout is not None else self._timeout)
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise ObsWsError(f"timed out waiting for op {op}")
            try:
                raw = ws.recv(timeout=remaining)
            except TimeoutError as exc:
                raise ObsWsError(f"timed out waiting for op {op}") from exc
            except ConnectionClosed as exc:
                if ws is self._ws:
                    self._ws = None
                raise ObsWsError(f"connection closed while waiting for op {op}: {exc}") from exc
            frame = json.loads(raw)
            # Discard events (op 5) and any frame not matching what we asked for.
            if frame.get("op") == op and matches(frame.get("d", {})):
                return frame["d"]
