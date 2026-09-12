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
from adapters.detector import Detector, to_source_rect
from adapters.frames import FrameSource
from adapters.obsws import ObsWs, ObsWsError
from core.geometry import Rect, clamp_to_source, expand, fit_ratio, to_crop, union
from core.policy import Command, PolicyParams, PolicyState, initial_state, step
from scripts.layout import (
    CAM_NAME,
    CONTROL_W,
    CONTROL_X,
    CONTROL_Y,
    FRAME_NAME,
    OUTPUT_H,
    OUTPUT_W,
    RATIO,
    SCENE_NAME,
)

# Shared with setup_scene.py's --control-port default, so the loop and the
# overlay Browser Source URL it bakes in can never drift apart.
DEFAULT_CONTROL_PORT = 4466
# Median committed-reframe amplitude on a 1920 px frame, measured in
# docs/poc-mac-webcam.md: the distance at which --ease-ms applies as-is.
REFERENCE_DISTANCE_PX = 675.0

DEFAULT_CONFIG_PATH = Path("reframe.toml")
# TOML has no null: password/log/media_file use "" for "not set", converted below.
_NULLABLE_STRING_KEYS = {"password", "log", "media_file"}

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
    },
}
# Dests scripts.run's own parser declares; scripts.setup_scene filters for its
# own subset instead, so one shared schema serves both without a second format.
RUN_CONFIG_DESTS = {dest for section in CONFIG_SCHEMA.values() for dest, _ in section.values()} - {
    "media_file",
    "camera",
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
    parser.add_argument("--detector", choices=("pose", "vision", "yolo"), default="pose")
    parser.add_argument(
        "--upper-body", action=argparse.BooleanOptionalAction, default=False,
        help="Vision : tête+torse au lieu du corps entier.",
    )
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--duration", type=float, default=0.0, help="0 = jusqu'à Ctrl-C")
    parser.add_argument("--log", default=None)
    parser.add_argument("--margin", type=float, default=defaults.margin)
    parser.add_argument("--min-crop-h", type=float, default=defaults.min_crop_h)
    parser.add_argument("--dead-zone", type=float, default=defaults.dead_zone)
    parser.add_argument("--dwell-ms", type=float, default=defaults.dwell_ms)
    parser.add_argument("--ease-ms", type=float, default=defaults.ease_ms, help="Durée à la distance de référence (675 px).")
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
        help="Extraction visage/pose (overlay OBS) : coûteuse (~20-25ms/image), désactivée par défaut.",
    )

    config_path = pre_args.config or DEFAULT_CONFIG_PATH
    status = apply_config(parser, config_path, explicit=pre_args.config is not None, dests=RUN_CONFIG_DESTS)
    args = parser.parse_args()
    print(status)
    return args


def build_detector(name: str, upper_body: bool) -> Detector:
    if name == "pose":
        from adapters.detect_pose import PoseDetector

        return PoseDetector(bust=upper_body)
    if name == "vision":
        from adapters.detect_vision import VisionDetector

        return VisionDetector(upper_body=upper_body)
    try:
        from adapters.detect_yolo import YoloDetector
    except ImportError as exc:
        print(f"Détecteur yolo indisponible ({exc}). Installez l'extra [yolo] ou utilisez --detector vision.")
        sys.exit(1)
    return YoloDetector()


def find_scene_items(obs: ObsWs, scene: str) -> tuple[int, int, int, int, int]:
    """Return (control_id, output_id, cell_top_id, cell_bottom_id, frame_id).

    Distinguished by bounds only, never by creation order: boundsWidth
    separates control from the three OUTPUT_W items, boundsHeight then
    separates the single output (OUTPUT_H) from the two cells (half that),
    and positionY separates the top cell from the bottom one.
    """
    items = obs.request("GetSceneItemList", {"sceneName": scene})["sceneItems"]
    cam_ids = [i["sceneItemId"] for i in items if i["sourceName"] == CAM_NAME]
    frame_ids = [i["sceneItemId"] for i in items if i["sourceName"] == FRAME_NAME]

    if len(cam_ids) != 4 or len(frame_ids) != 1:
        print(
            f"Scène « {scene} » incomplète (attendu 4 items {CAM_NAME} et 1 {FRAME_NAME}, "
            f"trouvé {len(cam_ids)} et {len(frame_ids)}). Lancez d'abord setup_scene --force."
        )
        sys.exit(1)

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
        print(
            f"Impossible d'identifier les items {CAM_NAME} par leurs bounds "
            f"(contrôle={len(control_ids)} sortie={len(output_ids)} cellules={len(cell_ids)}). "
            "Relancez setup_scene --force."
        )
        sys.exit(1)

    cell_top_id, cell_bottom_id = sorted(cell_ids, key=lambda i: transforms[i]["positionY"])
    return control_ids[0], output_ids[0], cell_top_id, cell_bottom_id, frame_ids[0]


def source_size(obs: ObsWs, scene: str, control_id: int) -> tuple[int, int]:
    transform = obs.request("GetSceneItemTransform", {"sceneName": scene, "sceneItemId": control_id})[
        "sceneItemTransform"
    ]
    return transform["sourceWidth"], transform["sourceHeight"]


def crop_patch(rect: Rect, source_w: int, source_h: int) -> dict:
    left, top, right, bottom = to_crop(rect, source_w, source_h)
    return {"cropLeft": left, "cropTop": top, "cropRight": right, "cropBottom": bottom}


def overlay_patch(rect: Rect, control_scale: float) -> dict:
    return {
        "positionX": CONTROL_X + rect.x * control_scale,
        "positionY": CONTROL_Y + rect.y * control_scale,
        "boundsWidth": rect.w * control_scale,
        "boundsHeight": rect.h * control_scale,
    }


def enabled_patch(scene: str, item_id: int, enabled: bool) -> tuple[str, dict]:
    return "SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": enabled}


def transform_patch(scene: str, item_id: int, transform: dict) -> tuple[str, dict]:
    return "SetSceneItemTransform", {"sceneName": scene, "sceneItemId": item_id, "sceneItemTransform": transform}


def make_single_apply_fn(
    scene: str,
    output_id: int,
    cell_top_id: int,
    cell_bottom_id: int,
    frame_id: int,
    source_w: int,
    source_h: int,
    control_scale: float,
) -> ApplyFn:
    """One rect drives the output crop and the control overlay; cells stay hidden."""
    def apply_fn(rects: tuple[Rect, ...]) -> list[tuple[str, dict]]:
        (rect,) = rects
        return [
            enabled_patch(scene, output_id, True),
            enabled_patch(scene, cell_top_id, False),
            enabled_patch(scene, cell_bottom_id, False),
            enabled_patch(scene, frame_id, True),
            transform_patch(scene, output_id, crop_patch(rect, source_w, source_h)),
            transform_patch(scene, frame_id, overlay_patch(rect, control_scale)),
        ]
    return apply_fn


def make_split_apply_fn(
    scene: str, output_id: int, cell_top_id: int, cell_bottom_id: int, frame_id: int, source_w: int, source_h: int
) -> ApplyFn:
    """Two rects drive the two cells; the single output and the overlay both hide."""
    def apply_fn(rects: tuple[Rect, ...]) -> list[tuple[str, dict]]:
        top, bottom = rects
        return [
            enabled_patch(scene, output_id, False),
            enabled_patch(scene, cell_top_id, True),
            enabled_patch(scene, cell_bottom_id, True),
            # RF Cadre is one rect: it cannot show both cells at once, so it
            # hides rather than draw a union that misrepresents the split.
            enabled_patch(scene, frame_id, False),
            transform_patch(scene, cell_top_id, crop_patch(top, source_w, source_h)),
            transform_patch(scene, cell_bottom_id, crop_patch(bottom, source_w, source_h)),
        ]
    return apply_fn


def travel_distance(frm: Rect, to: Rect) -> float:
    """Distance in source px, folding translation (center) and zoom (height)."""
    return math.dist((frm.cx, frm.cy, frm.h), (to.cx, to.cy, to.h))


def scaled_duration_ms(distance: float, reference_ms: float, min_ms: float, max_ms: float) -> float:
    """Duration proportional to distance, reference_ms at REFERENCE_DISTANCE_PX, clamped."""
    return max(min_ms, min(max_ms, reference_ms * distance / REFERENCE_DISTANCE_PX))


def emit_command(
    animator: Animator,
    cmd: Command,
    single_apply_fn: ApplyFn,
    split_apply_fn: ApplyFn,
    ease_min_ms: float,
    ease_max_ms: float,
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
    duration_ms = scaled_duration_ms(distance, cmd.duration_ms, ease_min_ms, ease_max_ms)
    animator.play((cmd.frm,), to, duration_ms, single_apply_fn)


def rect_dict(r: Rect | None) -> dict | None:
    return None if r is None else {"x": r.x, "y": r.y, "w": r.w, "h": r.h}


def state_dict(s: PolicyState) -> dict:
    return {
        "current": rect_dict(s.current),
        "pending": rect_dict(s.pending),
        "pending_since_ms": s.pending_since_ms,
        "last_seen_ms": s.last_seen_ms,
        "busy_until_ms": s.busy_until_ms,
    }


def raw_target(merged: Rect | None, p: PolicyParams) -> Rect | None:
    """Mirrors core.policy's internal target formula, for tracing even when no command fires."""
    if merged is None:
        return None
    return clamp_to_source(fit_ratio(expand(merged, p.margin), p.ratio), p.source_w, p.source_h, p.ratio, p.min_crop_h)


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
    detector = build_detector(args.detector, args.upper_body)

    obs = ObsWs(url=args.url, password=args.password)
    try:
        obs.connect()
        control_id, output_id, cell_top_id, cell_bottom_id, frame_id = find_scene_items(obs, args.scene)
        source_w, source_h = source_size(obs, args.scene, control_id)
    except ObsWsError as exc:
        print(f"Scène « {args.scene} » introuvable ou invalide ({exc}). Lancez d'abord setup_scene.")
        sys.exit(1)
    control_scale = CONTROL_W / source_w

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

    single_apply_fn = make_single_apply_fn(
        args.scene, output_id, cell_top_id, cell_bottom_id, frame_id, source_w, source_h, control_scale
    )
    split_apply_fn = make_split_apply_fn(
        args.scene, output_id, cell_top_id, cell_bottom_id, frame_id, source_w, source_h
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

    feature_extractor = None
    if args.features:
        from adapters.features import FeatureExtractor

        feature_extractor = FeatureExtractor()

    frames = FrameSource(obs, CAM_NAME, width=args.width)
    log_file = open(args.log, "w") if args.log else None

    stats: dict[str, list[float]] = {"capture": [], "detect": [], "policy": [], "send": [], "features": []}
    iterations = 0
    commands_emitted = 0
    current_fps = args.fps
    current_upper_body = args.upper_body
    target_period = 1.0 / current_fps
    start = time.perf_counter()
    deadline = None if args.duration <= 0 else start + args.duration
    next_tick = start

    try:
        while deadline is None or time.perf_counter() < deadline:
            try:
                paused, action = False, None
                if control is not None:
                    p, current_fps, upper_body_wanted, paused = control.get_controls()
                    action = control.pop_action()
                    if upper_body_wanted != current_upper_body:
                        detector = build_detector(args.detector, upper_body_wanted)
                        current_upper_body = upper_body_wanted
                        control.set_detector_name(detector.name)
                    target_period = 1.0 / current_fps

                t0 = time.perf_counter()
                jpeg = frames.grab()
                t1 = time.perf_counter()
                boxes = detector.detect(jpeg)
                t2 = time.perf_counter()

                features_payload = None
                if feature_extractor is not None:
                    tf0 = time.perf_counter()
                    features_payload = dataclasses.asdict(feature_extractor.extract(jpeg))
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
                    emit_command(animator, cmd, single_apply_fn, split_apply_fn, args.ease_min_ms, args.ease_max_ms)
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
                    if feature_extractor is not None:
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
                print(f"Connexion OBS perdue ({exc}), reconnexion...")
                if control is not None:
                    control.set_connected(False)
                obs.ensure_connected()
                control_id, output_id, cell_top_id, cell_bottom_id, frame_id = find_scene_items(obs, args.scene)
                if control is not None:
                    control.set_connected(True)

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
