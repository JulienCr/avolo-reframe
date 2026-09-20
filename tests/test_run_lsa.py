"""Tests for scripts.run_lsa's pure functions: child command lines and the
--live/--cams validation against scripts.layout_lsa.CAMERAS.
"""

from pathlib import Path

import pytest

from scripts.layout_lsa import CAMERAS
from scripts.run_lsa import camera_argv, director_argv, parse_cams, resolve_live


def test_camera_and_director_argv():
    config = Path("reframe.lsa.toml")
    live = camera_argv("main", config, live_key="main", extra=["--fps", "20"])
    assert live[3:7] == ["--cam", "main", "--config", "reframe.lsa.toml"]
    assert "--no-live" not in live
    assert live[-2:] == ["--fps", "20"]

    idle = camera_argv("cour", config, live_key="main", extra=[])
    assert "--no-live" in idle
    assert idle[-1] == "--no-live"

    director = director_argv(config, extra=["--duration", "60"])
    assert director[1:4] == ["-m", "scripts.director", "--config"]
    assert director[-2:] == ["--duration", "60"]


def test_validate_cams_and_live():
    assert parse_cams(None) == tuple(CAMERAS)
    assert parse_cams("cour, jardin") == ("cour", "jardin")
    with pytest.raises(ValueError):
        parse_cams("cour,bogus")

    assert resolve_live(None) == next(iter(CAMERAS))
    assert resolve_live("jardin") == "jardin"
    with pytest.raises(ValueError):
        resolve_live("bogus")
