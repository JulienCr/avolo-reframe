"""Builds the LSA 2026 vertical reframe scene in OBS: 3 cameras x (full + 2 split
cells) on the Aitum Vertical canvas, plus an optional debug scene on the main one.

Run as: uv run python -m scripts.setup_lsa
"""

import argparse
import sys
from pathlib import Path

from adapters.obsws import ObsWs, ObsWsError, SceneRef
from scripts import layout_lsa
from scripts.collection import refuse_if_output_active, require_scene_collection
from scripts.layout_lsa import (
    CAMERAS,
    CELL_H,
    CLONE_TYPE_SCENE,
    COLLECTION_NAME,
    DEBUG_SCENE_NAME,
    DEBUG_TILE_H,
    DEBUG_TILE_W,
    MAIN_CANVAS_NAME,
    VERTICAL_CANVAS_UUID,
    VERTICAL_H,
    VERTICAL_SCENE_NAME,
    VERTICAL_SCENE_UUID,
    VERTICAL_W,
    Camera,
)
from scripts.run import DEFAULT_CONFIG_PATH, apply_config
from scripts.setup_scene import (
    create_overlay_source,
    enforce_z_order,
    scene_exists,
    set_camera_view,
    wait_for_release,
)

SETUP_LSA_CONFIG_DESTS = {"url", "password"}

VERTICAL_REF: SceneRef = {"sceneUuid": VERTICAL_SCENE_UUID}
DEBUG_REF: SceneRef = {"sceneName": DEBUG_SCENE_NAME}


def parse_args() -> argparse.Namespace:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre_args, _ = pre.parse_known_args()

    parser = argparse.ArgumentParser(description="Construit la scène verticale LSA 2026 dans OBS.")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Fichier de config TOML (silencieux si absent)."
    )
    parser.add_argument("--url", default="ws://127.0.0.1:4455")
    parser.add_argument("--password", default=None)
    parser.add_argument("--force", action="store_true", help="Vide la scène verticale et reconstruit la scène debug.")
    parser.add_argument("--no-debug-scene", action="store_true", help="Ne construit pas la scène DEBUG - REFRAM.")

    config_path = pre_args.config or DEFAULT_CONFIG_PATH
    status = apply_config(parser, config_path, explicit=pre_args.config is not None, dests=SETUP_LSA_CONFIG_DESTS)
    args = parser.parse_args()
    print(status)
    return args


def check_vertical_canvas(obs: ObsWs) -> None:
    canvases = obs.request("GetCanvasList")["canvases"]
    canvas = next((c for c in canvases if c["canvasUuid"] == VERTICAL_CANVAS_UUID), None)
    if canvas is None:
        print(f"Aucun canevas d'uuid {VERTICAL_CANVAS_UUID} ({layout_lsa.VERTICAL_CANVAS_NAME} attendu).")
        sys.exit(1)

    settings = canvas["canvasVideoSettings"]
    size = (settings["baseWidth"], settings["baseHeight"])
    if size != (VERTICAL_W, VERTICAL_H):
        print(f"Canevas {layout_lsa.VERTICAL_CANVAS_NAME} en {size[0]}x{size[1]}, {VERTICAL_W}x{VERTICAL_H} attendu.")
        sys.exit(1)

    flags = ", ".join(name for name, active in canvas["canvasFlags"].items() if active) or "(aucun)"
    print(f"Canevas {layout_lsa.VERTICAL_CANVAS_NAME} : {size[0]}x{size[1]}, canvasFlags = {flags}")


def check_camera_scenes(obs: ObsWs) -> None:
    for cam in CAMERAS.values():
        try:
            obs.request("GetSceneItemList", {"sceneUuid": cam.scene_uuid})
        except ObsWsError as exc:
            print(f"Scène caméra « {cam.scene_name} » ({cam.key}, uuid {cam.scene_uuid}) inaccessible : {exc}")
            sys.exit(1)


def list_mirror_scenes(obs: ObsWs) -> list[dict]:
    """Every scene of the vertical canvas other than Vertical Scene itself:
    what an operator adds by hand (Coming, End, ...) for the vertical to mirror.
    """
    scenes = obs.request("GetSceneList", {"canvasUuid": VERTICAL_CANVAS_UUID})["scenes"]
    return [s for s in scenes if s["sceneUuid"] != VERTICAL_SCENE_UUID]


def create_mirror_item(obs: ObsWs, ref: SceneRef, scene: dict) -> int:
    """One full-canvas item for scene, disabled by default: director.py shows
    it only once the matching main-canvas scene becomes the program scene."""
    response = obs.request("CreateSceneItem", {**ref, "sourceUuid": scene["sceneUuid"], "sceneItemEnabled": False})
    item_id = response["sceneItemId"]
    set_camera_view(obs, ref, item_id, 0, 0, VERTICAL_W, VERTICAL_H)
    return item_id


def clear_vertical_scene(obs: ObsWs, force: bool) -> None:
    """Remove only what this script owns: camera clones and mirror items.

    Three families share the scene: camera clones (source is a source-clone
    input, identified by name), mirrors (source is a scene of the vertical
    canvas itself), and anything an operator adds by hand -- e.g. (Logo),
    whose source is a scene of the main canvas. The source's canvas is the
    discriminant, checked against OBS (GetSceneList per canvas) rather than
    assumed from a name.
    """
    items = obs.request("GetSceneItemList", VERTICAL_REF)["sceneItems"]
    owned_clone_names = {name for cam in CAMERAS.values() for name in cam.clone_names}
    mirror_scene_uuids = {s["sceneUuid"] for s in list_mirror_scenes(obs)}

    clone_items = [i for i in items if i["sourceName"] in owned_clone_names]
    mirror_items = [i for i in items if i["sourceUuid"] in mirror_scene_uuids]
    owned_ids = {i["sceneItemId"] for i in clone_items} | {i["sceneItemId"] for i in mirror_items}
    foreign = [i for i in items if i["sceneItemId"] not in owned_ids]

    if foreign:
        names = ", ".join(sorted({i["sourceName"] for i in foreign}))
        print(f"Items conservés (ajoutés hors de ce script) : {names}")
    if not clone_items and not mirror_items:
        return
    if not force:
        print(
            f"La scène « {VERTICAL_SCENE_NAME} » contient déjà {len(clone_items)} clone(s) caméra et "
            f"{len(mirror_items)} miroir(s). Relancez avec --force."
        )
        sys.exit(1)

    clone_names = sorted({i["sourceName"] for i in clone_items})
    for name in clone_names:
        obs.request("RemoveInput", {"inputName": name})
    wait_for_release(obs, None, clone_names)

    for item in mirror_items:
        obs.request("RemoveSceneItem", {**VERTICAL_REF, "sceneItemId": item["sceneItemId"]})


def create_camera_clone(obs: ObsWs, ref: SceneRef, clone_name: str, target_scene: str, enabled: bool) -> int:
    """Create a source-clone input wired as its own item, so OBS shows the
    operator clone_name rather than the shared target_scene's own name."""
    response = obs.request(
        "CreateInput",
        {
            **ref,
            "inputName": clone_name,
            "inputKind": "source-clone",
            "inputSettings": {
                "canvas": MAIN_CANVAS_NAME,
                "clone": target_scene,
                "clone_type": CLONE_TYPE_SCENE,
            },
            "sceneItemEnabled": enabled,
        },
    )
    return response["sceneItemId"]


def build_camera_items(obs: ObsWs, ref: SceneRef, cam: Camera) -> tuple[int, int, int]:
    """Create the plain clone plus the two split-cell clones. Returns their sceneItemIds."""
    full = create_camera_clone(obs, ref, cam.clone_plain_name, cam.scene_name, enabled=cam.key == "main")
    set_camera_view(obs, ref, full, 0, 0, VERTICAL_W, VERTICAL_H)

    top = create_camera_clone(obs, ref, cam.clone_split_top_name, cam.scene_name, enabled=False)
    set_camera_view(obs, ref, top, 0, 0, VERTICAL_W, CELL_H)

    bottom = create_camera_clone(obs, ref, cam.clone_split_bottom_name, cam.scene_name, enabled=False)
    set_camera_view(obs, ref, bottom, 0, CELL_H, VERTICAL_W, CELL_H)

    return full, top, bottom


def check_debug_scene_absent(obs: ObsWs, force: bool) -> None:
    """Guard run before any write, so a refusal never leaves half a build behind."""
    if not force and scene_exists(obs, DEBUG_SCENE_NAME):
        print(f"La scène « {DEBUG_SCENE_NAME} » existe déjà. Relancez avec --force pour la reconstruire.")
        sys.exit(1)


def rebuild_debug_scene(obs: ObsWs) -> None:
    if not scene_exists(obs, DEBUG_SCENE_NAME):
        return
    obs.request("RemoveScene", {"sceneName": DEBUG_SCENE_NAME})
    overlay_names = [cam.overlay_name for cam in CAMERAS.values()]
    existing_inputs = {i["inputName"] for i in obs.request("GetInputList")["inputs"]}
    removed = [name for name in overlay_names if name in existing_inputs]
    for name in removed:
        obs.request("RemoveInput", {"inputName": name})
    wait_for_release(obs, DEBUG_SCENE_NAME, removed)


def build_debug_scene(obs: ObsWs) -> dict[str, tuple[int, int]]:
    """Camera tiles at 16:9 (overlay.html scales by window size, a different
    ratio would distort the drawn detections), one browser overlay per camera."""
    rebuild_debug_scene(obs)
    obs.request("CreateScene", {"sceneName": DEBUG_SCENE_NAME})

    report: dict[str, tuple[int, int]] = {}
    for cam in CAMERAS.values():
        tile = obs.request("CreateSceneItem", {**DEBUG_REF, "sourceUuid": cam.scene_uuid, "sceneItemEnabled": True})[
            "sceneItemId"
        ]
        set_camera_view(obs, DEBUG_REF, tile, cam.debug_x, cam.debug_y, DEBUG_TILE_W, DEBUG_TILE_H)
        overlay = create_overlay_source(
            obs, DEBUG_REF, cam.overlay_name, cam.control_port, cam.debug_x, cam.debug_y, DEBUG_TILE_W, DEBUG_TILE_H
        )
        report[cam.key] = (tile, overlay)
    return report


def print_report(
    obs: ObsWs,
    items_by_cam: dict[str, tuple[int, int, int]],
    debug_report: dict[str, tuple[int, int]],
    mirror_items: dict[str, int],
) -> None:
    print("\n=== Scène verticale ===")
    for key, (full, top, bottom) in items_by_cam.items():
        cam = CAMERAS[key]
        size = obs.request("GetSceneItemTransform", {**VERTICAL_REF, "sceneItemId": full})["sceneItemTransform"]
        print(
            f"{key} : « {cam.clone_plain_name} »={full} « {cam.clone_split_top_name} »={top} "
            f"« {cam.clone_split_bottom_name} »={bottom} ({size['sourceWidth']}x{size['sourceHeight']})"
        )

    print("\n=== Miroirs (scènes du canevas vertical) ===")
    if not mirror_items:
        print("(aucune scène à mirer)")
    for name, item_id in mirror_items.items():
        size = obs.request("GetSceneItemTransform", {**VERTICAL_REF, "sceneItemId": item_id})["sceneItemTransform"]
        print(f"« {name} » item={item_id} ({size['sourceWidth']}x{size['sourceHeight']}), désactivé")

    print("\n=== Contrôle / overlays ===")
    for key, cam in CAMERAS.items():
        url = f"http://127.0.0.1:{cam.control_port}/overlay.html"
        print(f"{key} : port={cam.control_port} overlay={url}")
        if debug_report:
            tile, overlay = debug_report[key]
            print(f"  debug : tuile={tile} overlay_item={overlay}")


def main() -> None:
    args = parse_args()

    try:
        with ObsWs(url=args.url, password=args.password) as obs:
            require_scene_collection(obs, COLLECTION_NAME)
            refuse_if_output_active(obs, "construire la scène verticale")

            check_vertical_canvas(obs)
            check_camera_scenes(obs)
            if not args.no_debug_scene:
                check_debug_scene_absent(obs, args.force)
            clear_vertical_scene(obs, args.force)

            items_by_cam: dict[str, tuple[int, int, int]] = {}
            for cam in CAMERAS.values():
                items_by_cam[cam.key] = build_camera_items(obs, VERTICAL_REF, cam)

            mirror_items: dict[str, int] = {
                scene["sceneName"]: create_mirror_item(obs, VERTICAL_REF, scene) for scene in list_mirror_scenes(obs)
            }

            all_items = [item_id for triple in items_by_cam.values() for item_id in triple] + list(
                mirror_items.values()
            )
            enforce_z_order(obs, VERTICAL_REF, all_items)

            debug_report: dict[str, tuple[int, int]] = {}
            if not args.no_debug_scene:
                debug_report = build_debug_scene(obs)

            print_report(obs, items_by_cam, debug_report, mirror_items)
    except ObsWsError as exc:
        print(f"Erreur OBS : {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
