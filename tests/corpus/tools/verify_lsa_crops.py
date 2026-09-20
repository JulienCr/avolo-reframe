"""Read-only proof of application for the LSA multi-camera vertical reframe.

A second obs-websocket connection, independent from whichever scripts.run
instances are writing: the only proof this repo accepts is what a second
process reads back, never the rectangle a policy merely computed (see
CLAUDE.md, "Mesure"). Prints one line per camera every 500 ms, for
--duration seconds, and never issues a write request.

Run as: uv run python tests/corpus/tools/verify_lsa_crops.py [--duration 30]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from adapters.obsws import ObsWs, ObsWsError, SceneRef  # noqa: E402
from scripts.layout_lsa import CAMERAS, CELL_H, VERTICAL_H, VERTICAL_SCENE_UUID, VERTICAL_W  # noqa: E402
from scripts.run import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    SceneNotReady,
    Topology,
    apply_config,
    find_scene_items,
)

POLL_INTERVAL_S = 0.5


def build_topologies() -> dict[str, Topology]:
    """One Topology per production camera, mirroring scripts.run.build_topology's LSA branch."""
    return {
        key: Topology(
            name=cam.key,
            ref={"sceneUuid": VERTICAL_SCENE_UUID},
            image_source_name=cam.scene_name,
            image_source_uuid=cam.scene_uuid,
            source_uuid=cam.scene_uuid,
            full_bounds=(VERTICAL_W, VERTICAL_H),
            cell_bounds=(VERTICAL_W, CELL_H),
            control_w=None,
        )
        for key, cam in CAMERAS.items()
    }


def read_item(obs: ObsWs, ref: SceneRef, item_id: int) -> tuple[float, float, bool]:
    """(cropLeft, cropRight, sceneItemEnabled): two read-only requests, no write."""
    transform = obs.request("GetSceneItemTransform", {**ref, "sceneItemId": item_id})["sceneItemTransform"]
    enabled = obs.request("GetSceneItemEnabled", {**ref, "sceneItemId": item_id})["sceneItemEnabled"]
    return transform["cropLeft"], transform["cropRight"], enabled


def format_item(label: str, left: float, right: float, enabled: bool) -> str:
    return f"{label} enabled={str(enabled):5} cropL={left:6.1f} cropR={right:6.1f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Relit les crops et visibilités des 3x3 items LSA depuis un second processus."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--url", default="ws://127.0.0.1:4455")
    parser.add_argument("--password", default=None)
    parser.add_argument("--duration", type=float, default=30.0)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre_args, _ = pre.parse_known_args()
    config_path = pre_args.config or DEFAULT_CONFIG_PATH
    apply_config(parser, config_path, explicit=pre_args.config is not None, dests={"url", "password"})
    args = parser.parse_args()

    with ObsWs(url=args.url, password=args.password) as obs:
        try:
            resolved = {}
            for key, topo in build_topologies().items():
                topo, _control_id, full_id, cell_top_id, cell_bottom_id = find_scene_items(obs, topo)
                resolved[key] = (topo, full_id, cell_top_id, cell_bottom_id)
        except (SceneNotReady, ObsWsError) as exc:
            print(f"Impossible de résoudre les items caméra : {exc}")
            sys.exit(1)

        deadline = time.perf_counter() + args.duration
        while time.perf_counter() < deadline:
            for key, (topo, full_id, cell_top_id, cell_bottom_id) in resolved.items():
                try:
                    full = format_item("plein", *read_item(obs, topo.ref, full_id))
                    top = format_item("haut ", *read_item(obs, topo.ref, cell_top_id))
                    bottom = format_item("bas  ", *read_item(obs, topo.ref, cell_bottom_id))
                except ObsWsError as exc:
                    print(f"{key:>7} | illisible : {exc}")
                    continue
                print(f"{key:>7} | {full} | {top} | {bottom}")
            time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
