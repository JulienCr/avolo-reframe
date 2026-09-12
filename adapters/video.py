"""Deterministic JPEG frame decoding from a video file, via ffmpeg."""

import json
import subprocess
import threading
from dataclasses import dataclass
from typing import Iterator

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_CHUNK = 1 << 16


@dataclass(frozen=True)
class VideoFrame:
    index: int
    pts_ms: float
    jpeg: bytes


class VideoFrames:
    """Decodes a video file to JPEG frames at a fixed rate, deterministically."""

    def __init__(
        self,
        path: str,
        fps: float = 12.0,
        width: int = 640,
        start_s: float | None = None,
        duration_s: float | None = None,
    ) -> None:
        self._path = path
        self._fps = fps
        self._width = width
        self._start_s = start_s
        self._duration_s = duration_s

    def probe(self) -> dict:
        """Source width, height, duration and native frame rate, via ffprobe."""
        argv = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate:format=duration",
            "-of", "json", self._path,
        ]
        result = subprocess.run(argv, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed on {self._path}: {result.stderr}")
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        num, _, den = stream["r_frame_rate"].partition("/")
        return {
            "width": stream["width"],
            "height": stream["height"],
            "fps": float(num) / float(den or 1),
            "duration_s": float(data["format"]["duration"]),
        }

    def _ffmpeg_argv(self) -> list[str]:
        argv = ["ffmpeg", "-v", "error", "-nostdin"]
        if self._start_s is not None:
            argv += ["-ss", str(self._start_s)]
        argv += ["-i", self._path]
        if self._duration_s is not None:
            argv += ["-t", str(self._duration_s)]
        argv += [
            "-vf", f"fps={self._fps},scale={self._width}:-2",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-",
        ]
        return argv

    def __iter__(self) -> Iterator[VideoFrame]:
        proc = subprocess.Popen(
            self._ffmpeg_argv(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stderr_box: list[bytes] = []
        reader = threading.Thread(target=lambda: stderr_box.append(proc.stderr.read()))
        reader.start()

        frame_ms = 1000.0 / self._fps
        index = 0
        buf = b""
        abandoned = False
        try:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(_CHUNK)
                if not chunk:
                    break
                buf += chunk
                # A JPEG frame may straddle several reads; only emit a
                # complete SOI..EOI span, never a partial frame.
                start = buf.find(_SOI)
                while start >= 0:
                    end = buf.find(_EOI, start + 2)
                    if end < 0:
                        break
                    yield VideoFrame(index=index, pts_ms=index * frame_ms, jpeg=buf[start:end + 2])
                    index += 1
                    buf = buf[end + 2:]
                    start = buf.find(_SOI)
                if start > 0:
                    buf = buf[start:]
        except GeneratorExit:
            # Closing the pipe below makes an abandoned ffmpeg exit non-zero
            # on a broken pipe; that is expected, not a real failure.
            abandoned = True
            raise
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.terminate()
            reader.join()
            proc.wait()
            if not abandoned and proc.returncode != 0:
                stderr = b"".join(stderr_box).decode(errors="replace")
                raise RuntimeError(f"ffmpeg exited {proc.returncode} on {self._path}: {stderr}")
