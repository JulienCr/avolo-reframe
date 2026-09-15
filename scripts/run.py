"""Live loop: capture -> detect -> policy -> apply, against a scene built by setup_scene.

Run as: uv run python -m scripts.run
"""

import argparse
import dataclasses
import json
import math
import sys
import time
import tomllib
from pathlib import Path

from adapters.animator import Animator, ApplyFn
from adapters.control import ControlState, start_server
from adapters.detector import build_detector, to_source_rect
from adapters.frames import FrameSource
from adapters.obsws import ObsWs, ObsWsError
from core.geometry import Rect, clamp_to_source, expand, fit_ratio, to_crop, union
from core.policy import Command, PolicyParams, PolicyState, REFERENCE_SOURCE_H, height_floor, initial_state, step
from scripts.layout import AVOCAM_KIND, AVOCAM_PLACEHOLDER_SIZE, CAM_NAME, CONTROL_W, OUTPUT_H, OUTPUT_W, RATIO, SCENE_NAME

# Shared with setup_scene.py's --control-port default, so the loop and the
# overlay Browser Source URL it bakes in can never drift apart.
DEFAULT_CONTROL_PORT = 4466
# Median committed-reframe amplitude on a 1920 px frame, measured in
# docs/poc-mac-webcam.md: the distance at which --ease-ms applies as-is.
REFERENCE_DISTANCE_PX = 675.0
# GetSceneItemList costs roughly 0.1ms, negligible next to a ~5ms frame grab:
# cheap enough to poll this often, and frequent enough that a scene rebuild
# is caught before many frames land on whatever now holds the old ids.
SCENE_CHECK_INTERVAL_S = 2.0
SCENE_RESOLUTION_TIMEOUT_S = 3.0
SCENE_RESOLUTION_POLL_S = 0.1
# Measured in the OBS log on a rebuild: receiver started, first 4K frame 0.87s later.
AVOCAM_FIRST_FRAME_TIMEOUT_S = 5.0


class SceneNotReady(Exception):
    """Scene items missing, incomplete, or mid-rebuild; message is the French line to print."""


DEFAULT_CONFIG_PATH = Path("reframe.toml")
# TOML has no null: password/log/media_file/avocam_ip use "" for "not set", converted below.
_NULLABLE_STRING_KEYS = {"password", "log", "media_file", "avocam_ip"}

# section -> {toml key: (argparse dest, expected type(s))}. A key's dest can
# differ from its TOML spelling (split_enabled -> dest "split", matching the
# --split/--no-split flag) so run.py and setup_scene.py share one format
# while each only applies the dests its own parser declares.
CONFIG_SCHEMA: dict[str, dict[str, tuple[str, type | tuple[type, ...]]]] = {
    "connection": {
        "url": ("url", str),
        "password": ("password", str),
        "scene": ("scene", str),
        "control_port": ("control_port", int),
    },
    "detector": {
        "detector": ("detector", str),
        "upper_body": ("upper_body", bool),
        "yolo_model": ("yolo_model", str),
    },
    "loop": {
        "fps": ("fps", (int, float)),
        "width": ("width", int),
        "duration": ("duration", (int, float)),
        "log": ("log", str),
        "features": ("features", bool),
    },
    "policy": {
        "margin": ("margin", (int, float)),
        "min_crop_h": ("min_crop_h", (int, float)),
        "dead_zone": ("dead_zone", (int, float)),
        "dwell_ms": ("dwell_ms", (int, float)),
        "ease_ms": ("ease_ms", (int, float)),
        "ease_min_ms": ("ease_min_ms", (int, float)),
        "ease_max_ms": ("ease_max_ms", (int, float)),
        "snap": ("snap", bool),
        "hold_ms": ("hold_ms", (int, float)),
        "eye_line": ("eye_line", (int, float)),
        "zoom_dead_zone": ("zoom_dead_zone", (int, float)),
    },
    "split": {
        "split_enabled": ("split", bool),
        "split_min_gap": ("split_min_gap", (int, float)),
        "split_enter_ms": ("split_enter_ms", (int, float)),
        "split_exit_ms": ("split_exit_ms", (int, float)),
        "track_hold_ms": ("track_hold_ms", (int, float)),
    },
    "source": {
        "media_file": ("media_file", str),
        "camera": ("camera", bool),
        "avocam_ip": ("avocam_ip", str),
        "avocam_port": ("avocam_port", int),
    },
}
# Dests scripts.run's own parser declares; scripts.setup_scene filters for its
# own subset instead, so one shared schema serves both without a second format.
RUN_CONFIG_DESTS = {dest for section in CONFIG_SCHEMA.values() for dest, _ in section.values()} - {
    "media_file",
    "camera",
    "avocam_ip",
    "avocam_port",
}


def _type_matches(value: object, expected: type | tuple[type, ...]) -> bool:
    allowed = expected if isinstance(expected, tuple) else (expected,)
    # bool subclasses int in Python: without this, a stray `true` would pass
    # an int/float field, and no bool-typed field would ever reject a number.
    if isinstance(value, bool):
        return bool in allowed
    return isinstance(value, allowed)


def load_config(path: Path) -> dict[str, object]:
    """Parse and validate reframe.toml; unknown keys and wrong types are hard errors."""
    with path.open("rb") as f:
        raw = tomllib.load(f)

    flat: dict[str, object] = {}
    for section, table in raw.items():
        if section not in CONFIG_SCHEMA or not isinstance(table, dict):
            valid = ", ".join(sorted(CONFIG_SCHEMA))
            print(f"Section de config inconnue « {section} » dans {path}. Sections valides : {valid}.")
            sys.exit(1)
        schema = CONFIG_SCHEMA[section]
        for key, value in table.items():
            if key not in schema:
                valid = ", ".join(sorted(schema))
                print(f"Clé de config inconnue « {section}.{key} » dans {path}. Clés valides : {valid}.")
                sys.exit(1)
            dest, expected = schema[key]
            if not _type_matches(value, expected):
                want = expected if isinstance(expected, tuple) else (expected,)
                names = "/".join(t.__name__ for t in want)
                print(
                    f"Type invalide pour « {section}.{key} » dans {path} : "
                    f"attendu {names}, reçu {type(value).__name__}."
                )
                sys.exit(1)
            if dest == "detector" and value not in ("pose", "vision", "yolo"):
                print(f"Valeur invalide pour « {section}.{key} » dans {path} : « {value} » (pose, vision ou yolo).")
                sys.exit(1)
            flat[dest] = None if dest in _NULLABLE_STRING_KEYS and value == "" else value
    return flat


def apply_config(parser: argparse.ArgumentParser, config_path: Path, explicit: bool, dests: set[str]) -> str:
    """Load config_path if present and restrict it to dests as argparse defaults.

    Returns the status line; the caller prints it after parser.parse_args()
    so --help exits on its own output instead of this message first.
    """
    if not config_path.is_file():
        if explicit:
            print(f"Fichier de config « {config_path} » introuvable.")
            sys.exit(1)
        return f"Aucun fichier de configuration ({config_path}) : valeurs par défaut intégrées utilisées."
    values = {k: v for k, v in load_config(config_path).items() if k in dests}
    parser.set_defaults(**values)
    return f"Configuration chargée depuis {config_path}."


def parse_args() -> argparse.Namespace:
    defaults = PolicyParams()

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre_args, _ = pre.parse_known_args()

    parser = argparse.ArgumentParser(description="Boucle de recadrage en direct pour AVOLO Reframe.")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Fichier de config TOML (silencieux si absent)."
    )
    parser.add_argument("--url", default="ws://127.0.0.1:4455")
    parser.add_argument("--password", default=None)
    parser.add_argument("--scene", default=SCENE_NAME)
    parser.add_argument(
        "--detector", choices=("pose", "vision", "yolo"), default="pose" if sys.platform == "darwin" else "yolo"
    )
    parser.add_argument(
        "--upper-body", action=argparse.BooleanOptionalAction, default=False,
        help="Tête+torse au lieu du corps entier (buste pour pose et yolo).",
    )
    parser.add_argument("--yolo-model", default="models/yolo11m-pose.pt", help="Chemin du modèle yolo (.pt ou .engine).")
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--duration", type=float, default=0.0, help="0 = jusqu'à Ctrl-C")
    parser.add_argument("--log", default=None)
    parser.add_argument("--margin", type=float, default=defaults.margin)
    parser.add_argument(
        "--min-crop-h", type=float, default=defaults.min_crop_h,
        help="Pixels d'une source 1080p, mis à l'échelle selon la hauteur de la source.",
    )
    parser.add_argument("--dead-zone", type=float, default=defaults.dead_zone)
    parser.add_argument("--dwell-ms", type=float, default=defaults.dwell_ms)
    parser.add_argument(
        "--ease-ms", type=float, default=defaults.ease_ms,
        help="Durée à la distance de référence (675 px d'une source 1080p, mise à l'échelle).",
    )
    parser.add_argument("--ease-min-ms", type=float, default=180.0)
    parser.add_argument("--ease-max-ms", type=float, default=900.0)
    parser.add_argument("--snap", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--hold-ms", type=float, default=defaults.hold_ms)
    parser.add_argument("--eye-line", type=float, default=defaults.eye_line)
    parser.add_argument("--zoom-dead-zone", type=float, default=defaults.zoom_dead_zone)
    parser.add_argument(
        "--split",
        action=argparse.BooleanOptionalAction,
        default=defaults.split_enabled,
        help="Cadrage empilé à deux sujets (--no-split pour l'observer seul, hors politique split).",
    )
    parser.add_argument("--split-min-gap", type=float, default=defaults.split_min_gap)
    parser.add_argument("--split-enter-ms", type=float, default=defaults.split_enter_ms)
    parser.add_argument("--split-exit-ms", type=float, default=defaults.split_exit_ms)
    parser.add_argument("--track-hold-ms", type=float, default=defaults.track_hold_ms)
    parser.add_argument(
        "--control-port", type=int, default=DEFAULT_CONTROL_PORT, help="Port du dock de contrôle (0 pour désactiver)."
    )
    parser.add_argument(
        "--features",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Extraction visage/pose (overlay OBS) : coûteuse (~20-25ms/image) sur macOS (Apple Vision), "
        "quasi gratuite ailleurs (réutilise la détection de pose), désactivée par défaut.",
    )

    config_path = pre_args.config or DEFAULT_CONFIG_PATH
    status = apply_config(parser, config_path, explicit=pre_args.config is not None, dests=RUN_CONFIG_DESTS)
    args = parser.parse_args()
    print(status)
    return args


def find_scene_items(obs: ObsWs, scene: str) -> tuple[int, int, int, int, str]:
    """Return (control_id, output_id, cell_top_id, cell_bottom_id, cam_uuid).

    Distinguished by bounds only, never by creation order: boundsWidth
    separates control from the three OUTPUT_W items, boundsHeight then
    separates the single output (OUTPUT_H) from the two cells (half that),
    and positionY separates the top cell from the bottom one. cam_uuid is
    the sourceUuid shared by all four CAM_NAME items: a rebuild recreates
    the RF Cam input under the same name but a fresh uuid, which is what
    lets a caller tell a live scene from a rebuilt one with reused ids.
    """
    items = obs.request("GetSceneItemList", {"sceneName": scene})["sceneItems"]
    cam_items = [i for i in items if i["sourceName"] == CAM_NAME]

    if len(cam_items) != 4:
        raise SceneNotReady(
            f"Scène « {scene} » incomplète (attendu 4 items {CAM_NAME}, trouvé {len(cam_items)}). "
            "Lancez d'abord setup_scene --force."
        )

    cam_uuids = {i["sourceUuid"] for i in cam_items}
    if len(cam_uuids) != 1:
        raise SceneNotReady(
            f"Scène « {scene} » : les items {CAM_NAME} référencent des sources différentes "
            "(reconstruction en cours ?)"
        )
    cam_uuid = cam_uuids.pop()

    cam_ids = [i["sceneItemId"] for i in cam_items]
    transforms = {
        cam_id: obs.request("GetSceneItemTransform", {"sceneName": scene, "sceneItemId": cam_id})[
            "sceneItemTransform"
        ]
        for cam_id in cam_ids
    }
    control_ids = [i for i, t in transforms.items() if t["boundsWidth"] == CONTROL_W]
    output_ids = [i for i, t in transforms.items() if t["boundsWidth"] == OUTPUT_W and t["boundsHeight"] == OUTPUT_H]
    cell_ids = [i for i, t in transforms.items() if t["boundsWidth"] == OUTPUT_W and t["boundsHeight"] == OUTPUT_H / 2]

    if len(control_ids) != 1 or len(output_ids) != 1 or len(cell_ids) != 2:
        raise SceneNotReady(
            f"Impossible d'identifier les items {CAM_NAME} par leurs bounds "
            f"(contrôle={len(control_ids)} sortie={len(output_ids)} cellules={len(cell_ids)}). "
            "Relancez setup_scene --force."
        )

    cell_top_id, cell_bottom_id = sorted(cell_ids, key=lambda i: transforms[i]["positionY"])
    return control_ids[0], output_ids[0], cell_top_id, cell_bottom_id, cam_uuid


def source_size(obs: ObsWs, scene: str, control_id: int) -> tuple[int, int]:
    transform = obs.request("GetSceneItemTransform", {"sceneName": scene, "sceneItemId": control_id})[
        "sceneItemTransform"
    ]
    return transform["sourceWidth"], transform["sourceHeight"]


def should_wait_for_first_frame(
    kind: str, reported_size: tuple[int, int], current_size: tuple[int, int] | None
) -> bool:
    """Pure: True when a rebuilt AvoCam input reports the plugin's placeholder
    size while the loop still holds a real (non-placeholder) size.
    """
    return (
        kind == AVOCAM_KIND
        and reported_size == AVOCAM_PLACEHOLDER_SIZE
        and current_size is not None
        and current_size != AVOCAM_PLACEHOLDER_SIZE
    )


def resolve_scene(
    obs: ObsWs, scene: str, current_size: tuple[int, int] | None = None
) -> tuple[int, int, int, int, int, int, str]:
    """(control_id, output_id, cell_top_id, cell_bottom_id, source_w, source_h, cam_uuid).

    A scene rebuilt moments ago may not have renegotiated a resolution yet:
    poll briefly rather than handing back 0x0, which a caller later divides by.
    """
    control_id, output_id, cell_top_id, cell_bottom_id, cam_uuid = find_scene_items(obs, scene)
    deadline = time.perf_counter() + SCENE_RESOLUTION_TIMEOUT_S
    source_w, source_h = source_size(obs, scene, control_id)
    while (not source_w or not source_h) and time.perf_counter() < deadline:
        time.sleep(SCENE_RESOLUTION_POLL_S)
        source_w, source_h = source_size(obs, scene, control_id)
    if not source_w or not source_h:
        raise SceneNotReady(f"Scène « {scene} » : résolution pas encore négociée après reconstruction")

    cam_kind = obs.request("GetInputSettings", {"inputName": CAM_NAME})["inputKind"]
    if should_wait_for_first_frame(cam_kind, (int(source_w), int(source_h)), current_size):
        frame_deadline = time.perf_counter() + AVOCAM_FIRST_FRAME_TIMEOUT_S
        while (int(source_w), int(source_h)) == AVOCAM_PLACEHOLDER_SIZE and time.perf_counter() < frame_deadline:
            time.sleep(SCENE_RESOLUTION_POLL_S)
            source_w, source_h = source_size(obs, scene, control_id)

    return control_id, output_id, cell_top_id, cell_bottom_id, source_w, source_h, cam_uuid


def cam_items_match(items: list[dict], ids: tuple[int, int, int, int], cam_uuid: str) -> bool:
    """Pure: True when every id in ids names a CAM_NAME item carrying cam_uuid, in a
    GetSceneItemList-shaped list.

    A rebuild can reuse the same numeric ids and the same source name for a
    fresh input: sourceUuid is what actually changes, which name/id alone hide.
    """
    by_id = {i["sceneItemId"]: i for i in items}
    return all(
        (item := by_id.get(item_id)) is not None
        and item["sourceName"] == CAM_NAME
        and item.get("sourceUuid") == cam_uuid
        for item_id in ids
    )


def scene_items_match(obs: ObsWs, scene: str, ids: tuple[int, int, int, int], cam_uuid: str) -> bool:
    items = obs.request("GetSceneItemList", {"sceneName": scene})["sceneItems"]
    return cam_items_match(items, ids, cam_uuid)


def report_scene_retry(scene: str, exc: Exception, last_error: str | None, next_log_at: float) -> tuple[str, float]:
    """Rate-limited "scene unavailable" line, shared by the periodic check and the
    reconnect handler: a stuck rebuild logs at most once per SCENE_CHECK_INTERVAL_S.
    """
    msg = str(exc)
    now = time.perf_counter()
    if msg != last_error or now >= next_log_at:
        print(f"Scène « {scene} » indisponible ({msg}), nouvel essai.")
        next_log_at = now + SCENE_CHECK_INTERVAL_S
    return msg, next_log_at


def crop_patch(rect: Rect, source_w: int, source_h: int) -> dict:
    left, top, right, bottom = to_crop(rect, source_w, source_h)
    return {"cropLeft": left, "cropTop": top, "cropRight": right, "cropBottom": bottom}


def enabled_patch(scene: str, item_id: int, enabled: bool) -> tuple[str, dict]:
    return "SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": enabled}


def transform_patch(scene: str, item_id: int, transform: dict) -> tuple[str, dict]:
    return "SetSceneItemTransform", {"sceneName": scene, "sceneItemId": item_id, "sceneItemTransform": transform}


def make_single_apply_fn(
    scene: str,
    output_id: int,
    cell_top_id: int,
    cell_bottom_id: int,
    source_w: int,
    source_h: int,
) -> ApplyFn:
    """One rect drives the output crop; cells stay hidden."""
    def apply_fn(rects: tuple[Rect, ...]) -> list[tuple[str, dict]]:
        (rect,) = rects
        return [
            enabled_patch(scene, output_id, True),
            enabled_patch(scene, cell_top_id, False),
            enabled_patch(scene, cell_bottom_id, False),
            transform_patch(scene, output_id, crop_patch(rect, source_w, source_h)),
        ]
    return apply_fn


def make_split_apply_fn(
    scene: str, output_id: int, cell_top_id: int, cell_bottom_id: int, source_w: int, source_h: int
) -> ApplyFn:
    """Two rects drive the two cells; the single output hides."""
    def apply_fn(rects: tuple[Rect, ...]) -> list[tuple[str, dict]]:
        top, bottom = rects
        return [
            enabled_patch(scene, output_id, False),
            enabled_patch(scene, cell_top_id, True),
            enabled_patch(scene, cell_bottom_id, True),
            transform_patch(scene, cell_top_id, crop_patch(top, source_w, source_h)),
            transform_patch(scene, cell_bottom_id, crop_patch(bottom, source_w, source_h)),
        ]
    return apply_fn


def build_apply_fns(
    scene: str, output_id: int, cell_top_id: int, cell_bottom_id: int, source_w: int, source_h: int
) -> tuple[ApplyFn, ApplyFn]:
    return (
        make_single_apply_fn(scene, output_id, cell_top_id, cell_bottom_id, source_w, source_h),
        make_split_apply_fn(scene, output_id, cell_top_id, cell_bottom_id, source_w, source_h),
    )


def apply_source_resize(
    p: PolicyParams,
    control: ControlState | None,
    animator: Animator,
    scene: str,
    output_id: int,
    cell_top_id: int,
    cell_bottom_id: int,
    new_source_w: int,
    new_source_h: int,
) -> tuple[PolicyParams, PolicyState, ApplyFn, ApplyFn]:
    """Adopt a source's new size: the pixel-space policy state is reset, not
    rescaled; params and dock keep their tuning, only source_w/source_h change.
    """
    print(f"Taille de la source changée : {p.source_w}x{p.source_h} -> {new_source_w}x{new_source_h}.")
    p = dataclasses.replace(p, source_w=new_source_w, source_h=new_source_h)
    state = initial_state(p)
    if control is not None:
        control.replace_source_size(new_source_w, new_source_h)
    single_apply_fn, split_apply_fn = build_apply_fns(
        scene, output_id, cell_top_id, cell_bottom_id, new_source_w, new_source_h
    )
    animator.jump((state.current,), single_apply_fn)
    return p, state, single_apply_fn, split_apply_fn


def reapply_state(animator: Animator, state: PolicyState, single_apply_fn: ApplyFn, split_apply_fn: ApplyFn) -> None:
    """Cut OBS back to the policy's current framing, e.g. onto freshly rebuilt, uncropped items."""
    if state.mode == "split" and state.cells is not None:
        animator.jump(state.cells, split_apply_fn)
    else:
        animator.jump((state.current,), single_apply_fn)


def travel_distance(frm: Rect, to: Rect) -> float:
    """Distance in source px, folding translation (center) and zoom (height)."""
    return math.dist((frm.cx, frm.cy, frm.h), (to.cx, to.cy, to.h))


def scaled_duration_ms(distance: float, reference_ms: float, min_ms: float, max_ms: float, source_h: int) -> float:
    """Duration proportional to distance, reference_ms at REFERENCE_DISTANCE_PX
    (itself scaled to source_h, like height_floor), clamped.
    """
    reference_distance_px = REFERENCE_DISTANCE_PX * source_h / REFERENCE_SOURCE_H
    return max(min_ms, min(max_ms, reference_ms * distance / reference_distance_px))


def emit_command(
    animator: Animator,
    cmd: Command,
    single_apply_fn: ApplyFn,
    split_apply_fn: ApplyFn,
    ease_min_ms: float,
    ease_max_ms: float,
    source_h: int,
) -> None:
    if cmd.mode == "split":
        to = cmd.cells
        if cmd.frm_cells is None or cmd.duration_ms <= 0:
            animator.jump(to, split_apply_fn)
        else:
            animator.play(cmd.frm_cells, to, cmd.duration_ms, split_apply_fn)
        return

    to = (cmd.target,)
    if cmd.frm is None or cmd.duration_ms <= 0:
        animator.jump(to, single_apply_fn)
        return
    distance = travel_distance(cmd.frm, cmd.target)
    duration_ms = scaled_duration_ms(distance, cmd.duration_ms, ease_min_ms, ease_max_ms, source_h)
    animator.play((cmd.frm,), to, duration_ms, single_apply_fn)


def rect_dict(r: Rect | None) -> dict | None:
    return None if r is None else {"x": r.x, "y": r.y, "w": r.w, "h": r.h}


def cells_dict(cells: tuple[Rect, Rect] | None, source_w: int, source_h: int) -> list[dict] | None:
    """Normalize a (top, bottom) cell pair to fractions of the source, for the API."""
    if cells is None:
        return None
    return [{"x": r.x / source_w, "y": r.y / source_h, "w": r.w / source_w, "h": r.h / source_h} for r in cells]


def poses_features_dict(poses: list) -> dict:
    """Overlay payload shaped like adapters.features.Features, from poses already computed for this frame."""
    return {
        "faces": [],
        "bodies": [
            {
                "joints": pose.joints,
                "shoulder_angle_deg": pose.shoulder_angle_deg,
                "box": [pose.box.x, pose.box.y, pose.box.w, pose.box.h],
            }
            for pose in poses
        ],
        "timings_ms": {},
    }


def state_dict(s: PolicyState) -> dict:
    return {
        "current": rect_dict(s.current),
        "pending": rect_dict(s.pending),
        "pending_since_ms": s.pending_since_ms,
        "last_seen_ms": s.last_seen_ms,
        "busy_until_ms": s.busy_until_ms,
        "mode": s.mode,
    }


def raw_target(merged: Rect | None, p: PolicyParams) -> Rect | None:
    """Mirrors core.policy's internal target formula, for tracing even when no command fires."""
    if merged is None:
        return None
    return clamp_to_source(
        fit_ratio(expand(merged, p.margin), p.ratio), p.source_w, p.source_h, p.ratio, height_floor(p)
    )


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def print_summary(
    iterations: int, elapsed_s: float, stats: dict[str, list[float]], commands: int, detector_name: str
) -> None:
    rate = iterations / elapsed_s if elapsed_s > 0 else 0.0
    print(f"\nDétecteur : {detector_name}")
    print(f"{iterations} itérations en {elapsed_s:.1f}s ({rate:.2f} it/s), {commands} commandes émises.")
    labels = {
        "capture": "capture",
        "detect": "détection",
        "policy": "politique",
        "send": "envoi",
        "features": "traits",
    }
    for key, label in labels.items():
        values = stats[key]
        if not values:
            print(f"  {label}: aucune mesure")
            continue
        print(
            f"  {label}: min={min(values):.1f}ms médiane={percentile(values, 0.5):.1f}ms "
            f"p90={percentile(values, 0.9):.1f}ms"
        )


def main() -> None:
    args = parse_args()
    detector = build_detector(args.detector, args.upper_body, args.yolo_model)
    if args.features and sys.platform != "darwin" and not hasattr(detector, "detect_poses"):
        print(f"--features exige un détecteur qui expose detect_poses() ; « {detector.name} » ne l'expose pas.")
        sys.exit(1)

    obs = ObsWs(url=args.url, password=args.password)
    try:
        obs.connect()
        control_id, output_id, cell_top_id, cell_bottom_id, cam_uuid = find_scene_items(obs, args.scene)
        source_w, source_h = source_size(obs, args.scene, control_id)
        cam_kind = obs.request("GetInputSettings", {"inputName": CAM_NAME})["inputKind"]
        if cam_kind == AVOCAM_KIND and (source_w, source_h) == AVOCAM_PLACEHOLDER_SIZE:
            print(
                f"Attention : RF Cam annonce {source_w}x{source_h}, la taille du motif de test du plugin AvoCam -- "
                "la boucle adoptera la vraie taille dès la première image reçue."
            )
    except SceneNotReady as exc:
        print(str(exc))
        sys.exit(1)
    except ObsWsError as exc:
        print(f"Scène « {args.scene} » introuvable ou invalide ({exc}). Lancez d'abord setup_scene.")
        sys.exit(1)

    p = PolicyParams(
        source_w=source_w,
        source_h=source_h,
        ratio=RATIO,
        margin=args.margin,
        min_crop_h=args.min_crop_h,
        dead_zone=args.dead_zone,
        dwell_ms=args.dwell_ms,
        ease_ms=args.ease_ms,
        snap=args.snap,
        hold_ms=args.hold_ms,
        eye_line=args.eye_line,
        zoom_dead_zone=args.zoom_dead_zone,
        split_enabled=args.split,
        split_min_gap=args.split_min_gap,
        split_enter_ms=args.split_enter_ms,
        split_exit_ms=args.split_exit_ms,
        track_hold_ms=args.track_hold_ms,
    )
    state = initial_state(p)

    single_apply_fn, split_apply_fn = build_apply_fns(
        args.scene, output_id, cell_top_id, cell_bottom_id, source_w, source_h
    )

    animator = Animator(url=args.url, password=args.password)
    animator.start()
    animator.jump((state.current,), single_apply_fn)

    control = None
    if args.control_port:
        control = ControlState(params=p, detector_name=detector.name, fps=args.fps, upper_body=args.upper_body)
        scripts_dir = Path(__file__).parent
        start_server(control, args.control_port, scripts_dir / "dock.html", scripts_dir / "overlay.html")
        print(f"Tableau de bord : http://127.0.0.1:{args.control_port}/")
        print("Dans OBS : Docks → Custom Browser Docks, donnez-lui un nom, collez l'URL ci-dessus.")
        if args.features:
            print(f"Overlay traits : http://127.0.0.1:{args.control_port}/overlay.html")
            print(
                "Dans OBS : ajoutez une Browser Source avec cette URL, taille 1200x675, "
                "position (60, 202), au-dessus des items caméra."
            )
            if sys.platform != "darwin":
                print("Hors macOS : l'overlay ne montre que le squelette YOLO (pas de visage, pas de yaw).")

    feature_extractor = None
    if args.features and sys.platform == "darwin":
        from adapters.features import FeatureExtractor

        feature_extractor = FeatureExtractor()
    use_pose_features = args.features and feature_extractor is None

    frames = FrameSource(obs, CAM_NAME, width=args.width)
    log_file = open(args.log, "w", newline="\n") if args.log else None

    stats: dict[str, list[float]] = {"capture": [], "detect": [], "policy": [], "send": [], "features": []}
    iterations = 0
    commands_emitted = 0
    current_fps = args.fps
    current_upper_body = args.upper_body
    target_period = 1.0 / current_fps
    start = time.perf_counter()
    deadline = None if args.duration <= 0 else start + args.duration
    next_tick = start
    next_scene_check = start + SCENE_CHECK_INTERVAL_S
    last_scene_error: str | None = None
    next_scene_error_log = 0.0
    connection_lost_logged = False

    try:
        while deadline is None or time.perf_counter() < deadline:
            try:
                if time.perf_counter() >= next_scene_check:
                    ids = (control_id, output_id, cell_top_id, cell_bottom_id)
                    if not scene_items_match(obs, args.scene, ids, cam_uuid):
                        # A half-built rebuild (setup_scene creating items over some ms) must
                        # not kill the loop: retry locally, on the shared rate-limited line,
                        # rather than falling through to the "Connexion OBS perdue" handler.
                        try:
                            (
                                control_id, output_id, cell_top_id, cell_bottom_id,
                                new_source_w, new_source_h, cam_uuid,
                            ) = resolve_scene(obs, args.scene, current_size=(source_w, source_h))
                        except (SceneNotReady, ObsWsError) as resolve_exc:
                            last_scene_error, next_scene_error_log = report_scene_retry(
                                args.scene, resolve_exc, last_scene_error, next_scene_error_log
                            )
                            next_scene_check = time.perf_counter() + SCENE_RESOLUTION_POLL_S
                        else:
                            print(
                                "Scène reconstruite pendant que la boucle tournait : ré-résolution des scene items."
                            )
                            if (new_source_w, new_source_h) != (source_w, source_h):
                                p, state, single_apply_fn, split_apply_fn = apply_source_resize(
                                    p, control, animator, args.scene, output_id, cell_top_id, cell_bottom_id,
                                    new_source_w, new_source_h,
                                )
                                source_w, source_h = new_source_w, new_source_h
                            else:
                                single_apply_fn, split_apply_fn = build_apply_fns(
                                    args.scene, output_id, cell_top_id, cell_bottom_id, source_w, source_h
                                )
                                reapply_state(animator, state, single_apply_fn, split_apply_fn)
                            if control is not None:
                                control.set_connected(True)
                            last_scene_error = None
                            connection_lost_logged = False
                            next_scene_check = time.perf_counter() + SCENE_CHECK_INTERVAL_S
                    else:
                        new_source_w, new_source_h = source_size(obs, args.scene, control_id)
                        if new_source_w and new_source_h and (new_source_w, new_source_h) != (source_w, source_h):
                            p, state, single_apply_fn, split_apply_fn = apply_source_resize(
                                p, control, animator, args.scene, output_id, cell_top_id, cell_bottom_id,
                                new_source_w, new_source_h,
                            )
                            source_w, source_h = new_source_w, new_source_h
                        next_scene_check = time.perf_counter() + SCENE_CHECK_INTERVAL_S

                paused, action = False, None
                if control is not None:
                    p, current_fps, upper_body_wanted, paused = control.get_controls()
                    action = control.pop_action()
                    if upper_body_wanted != current_upper_body:
                        detector = build_detector(args.detector, upper_body_wanted, args.yolo_model)
                        current_upper_body = upper_body_wanted
                        control.set_detector_name(detector.name)
                    target_period = 1.0 / current_fps

                t0 = time.perf_counter()
                jpeg = frames.grab()
                t1 = time.perf_counter()
                if use_pose_features:
                    poses = detector.detect_poses(jpeg)
                    boxes = [p.box for p in poses]
                else:
                    boxes = detector.detect(jpeg)
                t2 = time.perf_counter()

                features_payload = None
                if feature_extractor is not None:
                    tf0 = time.perf_counter()
                    features_payload = dataclasses.asdict(feature_extractor.extract(jpeg))
                    stats["features"].append((time.perf_counter() - tf0) * 1000)
                elif use_pose_features:
                    tf0 = time.perf_counter()
                    features_payload = poses_features_dict(poses)
                    stats["features"].append((time.perf_counter() - tf0) * 1000)
                t2b = time.perf_counter()  # baseline after the optional features stage, before policy

                rects = [to_source_rect(b, source_w, source_h) for b in boxes]
                now_ms = time.perf_counter() * 1000
                if action == "recenter":
                    state = initial_state(p)
                    cmd = Command(target=state.current, frm=None, duration_ms=0.0, reason="recenter")
                elif not paused:
                    state, cmd = step(state, rects, now_ms, p)
                else:
                    cmd = None
                t3 = time.perf_counter()

                stats["capture"].append((t1 - t0) * 1000)
                stats["detect"].append((t2 - t1) * 1000)
                stats["policy"].append((t3 - t2b) * 1000)

                # A recenter always reaches OBS; an ordinary command is held back while paused.
                emitted = cmd is not None and (action == "recenter" or not paused)
                if emitted:
                    emit_command(
                        animator, cmd, single_apply_fn, split_apply_fn, args.ease_min_ms, args.ease_max_ms, source_h
                    )
                    stats["send"].append((time.perf_counter() - t3) * 1000)
                    commands_emitted += 1

                if control is not None:
                    stage_ms = {
                        "capture": stats["capture"][-1],
                        "detect": stats["detect"][-1],
                        "policy": stats["policy"][-1],
                    }
                    if emitted:
                        stage_ms["emit"] = stats["send"][-1]
                    if feature_extractor is not None or use_pose_features:
                        stage_ms["features"] = stats["features"][-1]
                    control.publish(
                        frame_jpeg=jpeg,
                        boxes=[{"x": b.x, "y": b.y, "w": b.w, "h": b.h, "score": b.score} for b in boxes],
                        crop={
                            "x": state.current.x / source_w,
                            "y": state.current.y / source_h,
                            "w": state.current.w / source_w,
                            "h": state.current.h / source_h,
                        },
                        stage_ms=stage_ms,
                        emitted=emitted,
                        mode=state.mode,
                        cells=cells_dict(state.cells, source_w, source_h),
                        features=features_payload,
                    )

                if log_file:
                    merged = union(rects)
                    entry = {
                        "ts": time.time(),
                        "detector": detector.name,
                        "n_detections": len(boxes),
                        "union": rect_dict(merged),
                        "target": rect_dict(raw_target(merged, p)),
                        "state": state_dict(state),
                        "emitted": rect_dict(cmd.target) if cmd is not None else None,
                    }
                    log_file.write(json.dumps(entry) + "\n")

                iterations += 1
            except ObsWsError as exc:
                # At most one line per failure episode: a scene mid-rebuild makes
                # frame grabs fail too, which would otherwise reprint this every tick.
                if not connection_lost_logged:
                    print(f"Connexion OBS perdue ({exc}), reconnexion...")
                    connection_lost_logged = True
                if control is not None:
                    control.set_connected(False)
                obs.ensure_connected()
                # Re-resolving ids alone is not enough: single/split_apply_fn
                # close over the old ones by value and must be rebuilt too.
                try:
                    (
                        control_id, output_id, cell_top_id, cell_bottom_id,
                        new_source_w, new_source_h, cam_uuid,
                    ) = resolve_scene(obs, args.scene, current_size=(source_w, source_h))
                except (SceneNotReady, ObsWsError) as resolve_exc:
                    # A scene mid-rebuild (setup_scene --force) makes this fail too: keep the
                    # stale ids and retry next iteration instead of killing the loop over it.
                    last_scene_error, next_scene_error_log = report_scene_retry(
                        args.scene, resolve_exc, last_scene_error, next_scene_error_log
                    )
                    time.sleep(SCENE_RESOLUTION_POLL_S)
                else:
                    if (new_source_w, new_source_h) != (source_w, source_h):
                        p, state, single_apply_fn, split_apply_fn = apply_source_resize(
                            p, control, animator, args.scene, output_id, cell_top_id, cell_bottom_id,
                            new_source_w, new_source_h,
                        )
                        source_w, source_h = new_source_w, new_source_h
                    else:
                        single_apply_fn, split_apply_fn = build_apply_fns(
                            args.scene, output_id, cell_top_id, cell_bottom_id, source_w, source_h
                        )
                        reapply_state(animator, state, single_apply_fn, split_apply_fn)
                    if control is not None:
                        control.set_connected(True)
                    last_scene_error = None
                    connection_lost_logged = False

            next_tick += target_period
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.perf_counter()
    except KeyboardInterrupt:
        pass
    finally:
        if log_file:
            log_file.close()
        animator.stop()
        obs.close()

    print_summary(iterations, time.perf_counter() - start, stats, commands_emitted, detector.name)


if __name__ == "__main__":
    main()
