"""Follows the main 16:9 program scene and switches the vertical reframe accordingly.

One CurrentProgramSceneChanged event per camera scene decides which of the four
scripts.run loops is live; every other loop is told no-live in the same switch.

Run as: uv run python -m scripts.director
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from adapters.obsws import ObsWs, ObsWsError
from scripts.layout_lsa import CAMERAS
from scripts.run import DEFAULT_CONFIG_PATH, apply_config

DIRECTOR_CONFIG_DESTS = {"url", "password"}

# EventSubscription::Scenes = (1 << 2), per obs-websocket v5's protocol.md;
# it carries CurrentProgramSceneChanged.
_EVENTS_SCENES = 1 << 2

_HTTP_TIMEOUT_S = 2.0

_SCENE_TO_CAMERA: dict[str, str] = {cam.scene_uuid: cam.key for cam in CAMERAS.values()}
_PORTS_BY_KEY: dict[str, int] = {cam.key: cam.control_port for cam in CAMERAS.values()}


def camera_for_scene(scene_uuid: str) -> str | None:
    """The camera whose output scene is scene_uuid, or None for any other scene."""
    return _SCENE_TO_CAMERA.get(scene_uuid)


def actions_for_switch(target_key: str) -> list[tuple[int, str]]:
    """(port, action) pairs: no-live to every other camera, live to the target."""
    actions = [
        (port, "no-live") for key, port in _PORTS_BY_KEY.items() if key != target_key
    ]
    actions.append((_PORTS_BY_KEY[target_key], "live"))
    return actions


def send_action(port: int, action: str, warned_ports: set[int]) -> None:
    """POST an action to one loop's control API; a missing loop is not fatal."""
    body = json.dumps({"action": action}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/action", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S).close()
    except (ConnectionRefusedError, urllib.error.URLError) as exc:
        if port not in warned_ports:
            print(f"Port {port} : boucle injoignable ({exc}), ignoré.")
            warned_ports.add(port)


def parse_args() -> argparse.Namespace:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre_args, _ = pre.parse_known_args()

    parser = argparse.ArgumentParser(description="Chef de pupitre : bascule la caméra live du canevas vertical.")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Fichier de config TOML (silencieux si absent)."
    )
    parser.add_argument("--url", default="ws://127.0.0.1:4455")
    parser.add_argument("--password", default=None)
    parser.add_argument("--duration", type=float, default=0.0, help="0 = jusqu'à Ctrl-C")

    config_path = pre_args.config or DEFAULT_CONFIG_PATH
    status = apply_config(parser, config_path, explicit=pre_args.config is not None, dests=DIRECTOR_CONFIG_DESTS)
    args = parser.parse_args()
    print(status)
    return args


def run(obs: ObsWs, duration: float) -> None:
    """Wait for CurrentProgramSceneChanged and switch the vertical canvas; never
    asks for the current scene (see CLAUDE.md: querying an empty program has
    coincided with an OBS crash), so nothing switches before the first event.
    """
    warned_ports: set[int] = set()
    deadline = None if duration <= 0 else time.perf_counter() + duration
    while deadline is None or time.perf_counter() < deadline:
        obs.ensure_connected()
        try:
            event = obs.next_event(timeout=1.0)
        except ObsWsError as exc:
            print(f"Connexion OBS perdue : {exc}")
            continue
        if event is None or event["eventType"] != "CurrentProgramSceneChanged":
            continue
        target = camera_for_scene(event["sceneUuid"])
        if target is None:
            continue
        actions = actions_for_switch(target)
        for port, action in actions:
            send_action(port, action, warned_ports)
        ports = ", ".join(str(port) for port, _ in actions)
        print(f"scène={event['sceneName']} -> caméra={target} (ports notifiés : {ports})")


def main() -> None:
    args = parse_args()
    try:
        with ObsWs(url=args.url, password=args.password, events=_EVENTS_SCENES) as obs:
            run(obs, args.duration)
    except ObsWsError as exc:
        print(f"Erreur OBS : {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
