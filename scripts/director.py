"""Follows the main 16:9 program scene and switches the vertical reframe accordingly.

One CurrentProgramSceneChanged event decides what the vertical shows: a camera
scene switches the live loop and hides every mirror, a same-named vertical
mirror scene shows that mirror and takes every loop off air, anything else is
left alone.

Run as: uv run python -m scripts.director
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from adapters.obsws import ObsWs, ObsWsError, SceneRef
from scripts import layout_lsa
from scripts.collection import require_scene_collection
from scripts.layout_lsa import CAMERAS
from scripts.run import DEFAULT_CONFIG_PATH, apply_config
from scripts.setup_lsa import list_mirror_scenes

DIRECTOR_CONFIG_DESTS = {"url", "password"}

# EventSubscription::Scenes = (1 << 2), per obs-websocket v5's protocol.md;
# it carries CurrentProgramSceneChanged.
_EVENTS_SCENES = 1 << 2

_HTTP_TIMEOUT_S = 2.0

_SCENE_TO_CAMERA: dict[str, str] = {cam.scene_uuid: cam.key for cam in CAMERAS.values()}
_PORTS_BY_KEY: dict[str, int] = {cam.key: cam.control_port for cam in CAMERAS.values()}

VERTICAL_REF: SceneRef = {"sceneUuid": layout_lsa.VERTICAL_SCENE_UUID}


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


def decide_switch(scene_uuid: str, scene_name: str, mirror_names: frozenset[str]) -> tuple[str | None, str | None]:
    """(camera_key, mirror_name): which family the program scene belongs to.

    At most one is set; both None when scene_uuid/scene_name match neither a
    camera nor a mirror -- case 3, nothing to do.
    """
    camera_key = camera_for_scene(scene_uuid)
    if camera_key is not None:
        return camera_key, None
    if scene_name in mirror_names:
        return None, scene_name
    return None, None


def mirror_visibility_patches(mirror_items: dict[str, int], target: str | None) -> list[tuple[int, bool]]:
    """(item_id, enabled) for every known mirror: target enabled, every other
    one disabled -- a full patch, never a delta, so a drift self-heals."""
    return [(item_id, name == target) for name, item_id in mirror_items.items()]


def read_mirror_items(obs: ObsWs) -> dict[str, int]:
    """{mirror scene name: its item id in Vertical Scene}, re-read on every
    switch so a scene added since startup (setup_lsa --force) needs no restart.
    """
    mirror_scenes = {s["sceneUuid"]: s["sceneName"] for s in list_mirror_scenes(obs)}
    items = obs.request("GetSceneItemList", VERTICAL_REF)["sceneItems"]
    return {mirror_scenes[i["sourceUuid"]]: i["sceneItemId"] for i in items if i["sourceUuid"] in mirror_scenes}


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
    require_scene_collection(obs, layout_lsa.COLLECTION_NAME)
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

        mirror_items = read_mirror_items(obs)
        camera_key, mirror_name = decide_switch(event["sceneUuid"], event["sceneName"], frozenset(mirror_items))
        if camera_key is None and mirror_name is None:
            continue

        actions = (
            actions_for_switch(camera_key)
            if camera_key is not None
            else [(port, "no-live") for port in _PORTS_BY_KEY.values()]
        )
        for port, action in actions:
            send_action(port, action, warned_ports)
        for item_id, enabled in mirror_visibility_patches(mirror_items, mirror_name):
            obs.request("SetSceneItemEnabled", {**VERTICAL_REF, "sceneItemId": item_id, "sceneItemEnabled": enabled})

        ports = ", ".join(str(port) for port, _ in actions)
        target = f"caméra={camera_key}" if camera_key is not None else f"miroir={mirror_name}"
        print(f"scène={event['sceneName']} -> {target} (ports notifiés : {ports})")


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
