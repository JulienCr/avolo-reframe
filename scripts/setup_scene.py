"""Builds the AVOLO Reframe POC scene in OBS: background, dual camera views, crop overlay.

Run as: uv run python -m scripts.setup_scene
"""

import argparse
import sys
import time
from pathlib import Path

from adapters.obsws import ObsWs, ObsWsError
from scripts.collection import ensure_scene_collection
from scripts.layout import (
    AVOCAM_KIND,
    AVOCAM_PLACEHOLDER_SIZE,
    BG_NAME,
    CAM_KIND,
    CAM_NAME,
    CANVAS_H,
    CANVAS_W,
    COLLECTION_NAME,
    CONTROL_H,
    CONTROL_W,
    CONTROL_X,
    CONTROL_Y,
    FRAME_NAME,
    MEDIA_KIND,
    OUTPUT_H,
    OUTPUT_W,
    OUTPUT_X,
    OUTPUT_Y,
    SCENE_NAME,
)
from scripts.run import DEFAULT_CONFIG_PATH, DEFAULT_CONTROL_PORT, apply_config

SETUP_SCENE_CONFIG_DESTS = {
    "url", "password", "control_port", "media_file", "camera", "avocam_ip", "avocam_port",
}

_DEVICE_PROPERTY = "video_device_id" if CAM_KIND == "dshow_input" else "device"

BG_COLOR = 0xFF1E1E1E
OVERLAY_NAME = "RF Overlay"
DEFAULT_MEDIA_FILE = "tests/fixtures/lab-avolo-58m22-70m00.mp4"
DEFAULT_AVOCAM_PORT = 5000
POLL_TIMEOUT_S = 8.0
AVOCAM_POLL_TIMEOUT_S = 15.0  # network connect + first keyframe; to be tuned by live measurement
POLL_INTERVAL_S = 0.25
TEARDOWN_TIMEOUT_S = 5.0


def parse_args() -> argparse.Namespace:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre_args, _ = pre.parse_known_args()

    parser = argparse.ArgumentParser(description="Construit la scène AVOLO Reframe POC dans OBS.")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Fichier de config TOML (silencieux si absent)."
    )
    parser.add_argument("--url", default="ws://127.0.0.1:4455")
    parser.add_argument("--password", default=None)
    parser.add_argument("--force", action="store_true", help="Détruit et reconstruit la scène existante.")
    parser.add_argument("--device", default=None, help="Sous-chaîne (insensible à la casse) du nom de la caméra.")
    parser.add_argument("--allow-center-stage", action="store_true")
    parser.add_argument(
        "--media-file",
        default=None,
        help=f"Fichier vidéo à boucler (défaut : {DEFAULT_MEDIA_FILE}).",
    )
    parser.add_argument(
        "--camera",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Utilise une caméra au lieu du fichier vidéo par défaut.",
    )
    parser.add_argument(
        "--avocam-ip",
        default=None,
        help=(
            "IP de l'iPhone AvoCam ; avec --camera, RF Cam devient une source AvoCam "
            "au lieu d'un périphérique de capture local."
        ),
    )
    parser.add_argument(
        "--avocam-port",
        type=int,
        default=DEFAULT_AVOCAM_PORT,
        help="Port UDP attribué à cette caméra par le contrôleur AvoCam.",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=DEFAULT_CONTROL_PORT,
        help="Port du serveur de contrôle, pour l'URL de l'overlay RF Overlay (doit correspondre à scripts.run).",
    )

    config_path = pre_args.config or DEFAULT_CONFIG_PATH
    status = apply_config(parser, config_path, explicit=pre_args.config is not None, dests=SETUP_SCENE_CONFIG_DESTS)
    args = parser.parse_args()
    print(status)

    wants_camera = args.camera or args.device or args.allow_center_stage
    if args.media_file and wants_camera:
        print("--media-file est incompatible avec --camera, --device et --allow-center-stage.")
        sys.exit(1)

    if not (1024 <= args.avocam_port <= 65535):
        print(f"--avocam-port doit être compris entre 1024 et 65535 (reçu {args.avocam_port}).")
        sys.exit(1)

    # The fixture is the default source: a camera makes every replay different,
    # so measurements taken against one are not comparable between runs.
    if not wants_camera and not args.media_file:
        fixture = Path(DEFAULT_MEDIA_FILE)
        if fixture.is_file():
            args.media_file = str(fixture)
        else:
            print(f"Fichier par défaut « {DEFAULT_MEDIA_FILE} » absent : bascule sur la caméra.")

    return args


def normalize(name: str) -> str:
    """Strip non-breaking spaces so French device names match cleanly."""
    return name.replace("\xa0", " ")


def scene_exists(obs: ObsWs) -> bool:
    scenes = obs.request("GetSceneList")["scenes"]
    return any(s["sceneName"] == SCENE_NAME for s in scenes)


def teardown_existing_scene(obs: ObsWs) -> None:
    obs.request("RemoveScene", {"sceneName": SCENE_NAME})
    existing_inputs = {i["inputName"] for i in obs.request("GetInputList")["inputs"]}
    removed = [name for name in (FRAME_NAME, CAM_NAME, BG_NAME, OVERLAY_NAME) if name in existing_inputs]
    for name in removed:
        obs.request("RemoveInput", {"inputName": name})

    # obs-websocket removals complete asynchronously: a rebuild started too soon
    # can collide with a scene or source name OBS has not actually released yet.
    deadline = time.perf_counter() + TEARDOWN_TIMEOUT_S
    stuck: list[str] = []
    while time.perf_counter() < deadline:
        scenes = {s["sceneName"] for s in obs.request("GetSceneList")["scenes"]}
        inputs = {i["inputName"] for i in obs.request("GetInputList")["inputs"]}
        stuck = ([SCENE_NAME] if SCENE_NAME in scenes else []) + [n for n in removed if n in inputs]
        if not stuck:
            return
        time.sleep(POLL_INTERVAL_S)
    print(f"OBS n'a pas libéré {', '.join(stuck)} après {TEARDOWN_TIMEOUT_S:g}s.")
    sys.exit(1)


def create_color_source(obs: ObsWs, name: str, color: int) -> int:
    response = obs.request(
        "CreateInput",
        {
            "sceneName": SCENE_NAME,
            "inputName": name,
            "inputKind": "color_source_v3",
            "inputSettings": {"color": color, "width": CANVAS_W, "height": CANVAS_H},
            "sceneItemEnabled": True,
        },
    )
    return response["sceneItemId"]


def set_transform(obs: ObsWs, item_id: int, patch: dict) -> None:
    obs.request(
        "SetSceneItemTransform",
        {"sceneName": SCENE_NAME, "sceneItemId": item_id, "sceneItemTransform": patch},
    )


def set_camera_view(obs: ObsWs, item_id: int, x: float, y: float, w: float, h: float) -> None:
    set_transform(
        obs,
        item_id,
        {
            "positionX": x,
            "positionY": y,
            "alignment": 5,
            "boundsType": "OBS_BOUNDS_SCALE_INNER",
            "boundsAlignment": 0,
            "boundsWidth": w,
            "boundsHeight": h,
        },
    )


def pick_device(obs: ObsWs, substring: str | None) -> dict:
    items = obs.request(
        "GetInputPropertiesListPropertyItems",
        {"inputName": CAM_NAME, "propertyName": _DEVICE_PROPERTY},
    )["propertyItems"]

    if substring:
        needle = substring.lower()
        matches = [i for i in items if needle in normalize(i["itemName"]).lower()]
        if not matches:
            print(f"Aucune caméra ne correspond à « {substring} ».")
            sys.exit(1)
        return matches[0]

    # Default: a Continuity iPhone camera (never its Desk View companion),
    # else the first real camera, never the virtual one we would feed back.
    for item in items:
        name = normalize(item["itemName"]).lower()
        if "iphone" in name and "desk view" not in name:
            return item
    for item in items:
        name = normalize(item["itemName"]).lower()
        if "virtual camera" not in name:
            return item

    print("Aucune caméra réelle disponible sur cette machine.")
    sys.exit(1)


def check_center_stage(device_uuid: str, allow: bool) -> None:
    try:
        import AVFoundation as AV

        session = AV.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
            [
                "AVCaptureDeviceTypeBuiltInWideAngleCamera",
                "AVCaptureDeviceTypeExternal",
                "AVCaptureDeviceTypeContinuityCamera",
            ],
            AV.AVMediaTypeVideo,
            0,
        )
        active = any(
            str(d.uniqueID()) == device_uuid and d.isCenterStageActive() for d in session.devices()
        )
    except Exception as exc:
        print(f"Avertissement : vérification Center Stage impossible ({exc}).")
        return

    if active and not allow:
        print("Center Stage est actif sur cette caméra : les mesures seraient faussées.")
        print("Désactivez-le, ou relancez avec --allow-center-stage pour poursuivre quand même.")
        sys.exit(1)
    if active:
        print("Attention : Center Stage est actif ; poursuite forcée via --allow-center-stage.")
    else:
        print("Center Stage : inactif.")


def wait_for_resolution(
    obs: ObsWs,
    item_id: int,
    timeout_s: float = POLL_TIMEOUT_S,
    failure_hint: str | None = None,
    placeholder: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Poll sourceWidth/sourceHeight until they settle.

    placeholder marks a size that is not trustworthy on its own (the AvoCam
    plugin's test-pattern size): polling continues past it, and a timeout
    still stuck there is reported but not fatal, unlike a genuine 0x0.
    """
    deadline = time.perf_counter() + timeout_s
    w, h = 0, 0
    while time.perf_counter() < deadline:
        transform = obs.request(
            "GetSceneItemTransform", {"sceneName": SCENE_NAME, "sceneItemId": item_id}
        )["sceneItemTransform"]
        w, h = transform["sourceWidth"], transform["sourceHeight"]
        if w and h and (placeholder is None or (int(w), int(h)) != placeholder):
            return int(w), int(h)
        time.sleep(POLL_INTERVAL_S)
    if not w or not h:
        print(f"La source n'a jamais négocié de format (0x0 après {timeout_s:g}s).")
        if failure_hint:
            print(failure_hint)
        sys.exit(1)
    print(
        f"Attention : la source annonce toujours {int(w)}x{int(h)} après {timeout_s:g}s, la taille du motif de "
        "test du plugin AvoCam. Soit l'iPhone diffuse bien en 1080p, soit aucune image n'est encore arrivée "
        "(vérifiez l'iPhone) ; poursuite avec cette taille en attendant."
    )
    return int(w), int(h)


def enforce_z_order(
    obs: ObsWs, bg: int, control: int, output: int, cell_top: int, cell_bottom: int, overlay: int
) -> None:
    # Index 0 is the bottom of the render stack; higher indices draw on top.
    for index, item_id in enumerate((bg, control, output, cell_top, cell_bottom, overlay)):
        obs.request(
            "SetSceneItemIndex",
            {"sceneName": SCENE_NAME, "sceneItemId": item_id, "sceneItemIndex": index},
        )


def media_input_settings(path: str) -> dict:
    return {
        "local_file": path,
        "is_local_file": True,
        "looping": True,
        # close_when_inactive/restart_on_activate off keep the file decoding continuously,
        # like a camera, instead of rewinding on every scene switch (restart_on_activate
        # would corrupt the scene-switch behaviour the POC exists to test).
        "close_when_inactive": False,
        "restart_on_activate": False,
        "hw_decode": True,
    }


def create_overlay_source(obs: ObsWs, control_port: int) -> int:
    """Transparent Browser Source polling /api/state and /api/features, drawing
    detections and the current framing over the control view."""
    response = obs.request(
        "CreateInput",
        {
            "sceneName": SCENE_NAME,
            "inputName": OVERLAY_NAME,
            "inputKind": "browser_source",
            "inputSettings": {
                "url": f"http://127.0.0.1:{control_port}/overlay.html",
                "width": CONTROL_W,
                "height": CONTROL_H,
                "shutdown": False,
                "restart_when_active": False,
            },
            "sceneItemEnabled": True,
        },
    )
    item_id = response["sceneItemId"]
    set_transform(
        obs,
        item_id,
        {
            "positionX": CONTROL_X,
            "positionY": CONTROL_Y,
            "alignment": 5,
            "boundsType": "OBS_BOUNDS_STRETCH",
            "boundsAlignment": 0,
            "boundsWidth": CONTROL_W,
            "boundsHeight": CONTROL_H,
        },
    )
    return item_id


def check_avocam_port_conflict(obs: ObsWs, port: int) -> None:
    """The AvoCam plugin reserves its UDP port at start and frees it only when
    the source is deleted: a second input on the same port renders black."""
    inputs = obs.request("GetInputList", {"inputKind": AVOCAM_KIND})["inputs"]
    for item in inputs:
        name = item["inputName"]
        if name == CAM_NAME:
            continue
        settings = obs.request("GetInputSettings", {"inputName": name})["inputSettings"]
        other_port = settings.get("manual_port", DEFAULT_AVOCAM_PORT)
        if other_port == port:
            print(
                f"La source AvoCam « {name} » occupe déjà le port UDP {port}. "
                "Le plugin AvoCam garde un port réservé tant que sa source n'est pas supprimée : "
                "supprimez-la, ou choisissez un autre port avec --avocam-port."
            )
            sys.exit(1)


def build_scene(
    obs: ObsWs,
    device: str | None,
    allow_center_stage: bool,
    media_file: str | None = None,
    control_port: int = DEFAULT_CONTROL_PORT,
    avocam_ip: str | None = None,
    avocam_port: int = DEFAULT_AVOCAM_PORT,
) -> None:
    if avocam_ip:
        check_avocam_port_conflict(obs, avocam_port)

    obs.request("CreateScene", {"sceneName": SCENE_NAME})

    bg_item = create_color_source(obs, BG_NAME, BG_COLOR)
    set_transform(obs, bg_item, {"positionX": 0, "positionY": 0, "alignment": 5})

    if media_file:
        cam_kind, cam_settings = MEDIA_KIND, media_input_settings(media_file)
    elif avocam_ip:
        cam_kind, cam_settings = AVOCAM_KIND, {"manual_ip": avocam_ip, "manual_port": avocam_port}
    else:
        cam_kind, cam_settings = CAM_KIND, {}

    cam_response = obs.request(
        "CreateInput",
        {
            "sceneName": SCENE_NAME,
            "inputName": CAM_NAME,
            "inputKind": cam_kind,
            "inputSettings": cam_settings,
            "sceneItemEnabled": True,
        },
    )
    control_item = cam_response["sceneItemId"]

    if media_file:
        print(f"Source choisie : fichier vidéo en boucle ({media_file})")
    elif avocam_ip:
        print(f"Source choisie : AvoCam {avocam_ip}:{avocam_port}")
    else:
        chosen = pick_device(obs, device)
        obs.request(
            "SetInputSettings",
            {"inputName": CAM_NAME, "inputSettings": {_DEVICE_PROPERTY: chosen["itemValue"]}},
        )
        print(f"Caméra choisie : {normalize(chosen['itemName'])} ({chosen['itemValue']})")
        if sys.platform == "darwin":
            check_center_stage(chosen["itemValue"], allow_center_stage)
        else:
            print("Center Stage : non applicable (spécifique à macOS).")

    set_camera_view(obs, control_item, CONTROL_X, CONTROL_Y, CONTROL_W, CONTROL_H)

    output_item = obs.request(
        "CreateSceneItem", {"sceneName": SCENE_NAME, "sourceName": CAM_NAME, "sceneItemEnabled": True}
    )["sceneItemId"]
    set_camera_view(obs, output_item, OUTPUT_X, OUTPUT_Y, OUTPUT_W, OUTPUT_H)

    # Split mode's two stacked cells: same input, two more views, hidden
    # until split mode shows them — single mode is the starting state.
    cell_h = OUTPUT_H // 2
    cell_top_item = obs.request(
        "CreateSceneItem", {"sceneName": SCENE_NAME, "sourceName": CAM_NAME, "sceneItemEnabled": False}
    )["sceneItemId"]
    set_camera_view(obs, cell_top_item, OUTPUT_X, OUTPUT_Y, OUTPUT_W, cell_h)
    cell_bottom_item = obs.request(
        "CreateSceneItem", {"sceneName": SCENE_NAME, "sourceName": CAM_NAME, "sceneItemEnabled": False}
    )["sceneItemId"]
    set_camera_view(obs, cell_bottom_item, OUTPUT_X, OUTPUT_Y + cell_h, OUTPUT_W, cell_h)

    overlay_item = create_overlay_source(obs, control_port)

    enforce_z_order(obs, bg_item, control_item, output_item, cell_top_item, cell_bottom_item, overlay_item)

    if avocam_ip:
        # The AvoCam plugin only starts receiving once its scene item is in program.
        obs.request("SetCurrentProgramScene", {"sceneName": SCENE_NAME})
        print(f"Scène « {SCENE_NAME} » mise en direct pour démarrer la réception AvoCam.")
        timeout_s = AVOCAM_POLL_TIMEOUT_S
        failure_hint = (
            "Vérifiez que l'iPhone diffuse bien vers cette machine sur ce port, "
            "et qu'aucune autre source AvoCam ne retient déjà le port."
        )
        placeholder = AVOCAM_PLACEHOLDER_SIZE
    else:
        timeout_s = POLL_TIMEOUT_S
        failure_hint = None
        placeholder = None

    source_w, source_h = wait_for_resolution(obs, control_item, timeout_s, failure_hint, placeholder)
    print(f"Résolution négociée : {source_w}x{source_h}")
    print(
        f"Identifiants des scene items : fond={bg_item} contrôle={control_item} "
        f"sortie={output_item} cellule_haut={cell_top_item} cellule_bas={cell_bottom_item} "
        f"overlay={overlay_item}"
    )
    print(
        "Overlay RF Overlay ajouté : la couche de cadrage s'affiche même sans --features ; "
        "tant que le port n'est pas encore servi, il affiche « serveur injoignable »."
    )


def main() -> None:
    args = parse_args()

    media_file = None
    if args.media_file:
        # ffmpeg_source renders black on a relative local_file with no error —
        # always resolve to an absolute path before it reaches OBS.
        media_path = Path(args.media_file).resolve()
        if not media_path.is_file():
            print(f"Le fichier vidéo « {media_path} » n'existe pas.")
            sys.exit(1)
        media_file = str(media_path)

    # A local device takes priority over avocam_ip; avocam_ip set with no --camera
    # (i.e. media_file still resolved to the fixture) never switches the source.
    avocam_ip = args.avocam_ip if (media_file is None and not args.device) else None

    try:
        with ObsWs(url=args.url, password=args.password) as obs:
            ensure_scene_collection(obs, COLLECTION_NAME)
            if scene_exists(obs):
                if not args.force:
                    print(f"La scène « {SCENE_NAME} » existe déjà. Relancez avec --force pour la reconstruire.")
                    sys.exit(1)
                teardown_existing_scene(obs)
            try:
                build_scene(
                    obs,
                    args.device,
                    args.allow_center_stage,
                    media_file,
                    args.control_port,
                    avocam_ip,
                    args.avocam_port,
                )
            except ObsWsError as exc:
                print(f"Erreur OBS : {exc}")
                # A camera pick or property lookup can fail mid-build, leaving
                # the scene and its sources half-created: clean up before exiting.
                if scene_exists(obs):
                    teardown_existing_scene(obs)
                sys.exit(1)
            except SystemExit as exc:
                # pick_device, the Center Stage guard and wait_for_resolution
                # exit(1) directly, bypassing the except above: clean up here too.
                if exc.code not in (0, None) and scene_exists(obs):
                    try:
                        teardown_existing_scene(obs)
                    except ObsWsError as cleanup_exc:
                        print(f"Erreur OBS pendant le nettoyage : {cleanup_exc}")
                raise
    except ObsWsError as exc:
        print(f"Erreur OBS : {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
