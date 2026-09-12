"""Go/no-go diagnostic for the out-of-process obs-websocket reframing approach.

Reads OBS environment and canvas state, cross-references cameras against
Center Stage, measures round-trip latencies, and verifies with a pixel
comparison that a crop pushed via SetSceneItemTransform is actually
rendered. Exit code reflects the three go/no-go items from the ADR.
"""

import argparse
import hashlib
import statistics
import sys
import time

from adapters.frames import FrameSource
from adapters.obsws import ObsWs, ObsWsError

_LABEL_WIDTH = 34

_PROBE_PATTERN_SCENE = "RF Probe Pattern"
_PROBE_HOST_SCENE = "RF Probe Host"
_PROBE_QUADRANT_NAMES = ("RF Probe Q1", "RF Probe Q2", "RF Probe Q3", "RF Probe Q4")
# 0xAABBGGRR: color_source_v3 expects alpha-blue-green-red, not RGB.
_QUADRANT_COLORS = (0xFFE6194B, 0xFF3CB44B, 0xFF4363D8, 0xFFFFE119)
_REAL_SCENE = "AVOLO Reframe POC"
_REAL_SOURCE = "RF Cam"


def _row(label: str, value: object) -> str:
    # A plain :< pad collapses to zero separation once label >= _LABEL_WIDTH.
    pad = " " * max(1, _LABEL_WIDTH - len(label))
    return f"{label}{pad}{value}"


def _version_at_least(version: str, minimum: tuple[int, int]) -> bool:
    parts = tuple(int(p) for p in version.split(".")[:2])
    return parts >= minimum


def check_environnement(obs: ObsWs) -> bool:
    print("\n=== 1. Environnement ===")
    version = obs.request("GetVersion")
    requests = version["availableRequests"]
    has_create_canvas = "CreateCanvas" in requests
    version_ok = _version_at_least(version["obsVersion"], (32, 1))

    print(_row("OBS", version["obsVersion"]))
    print(_row("obs-websocket", version["obsWebSocketVersion"]))
    print(_row("rpcVersion négocié", version["rpcVersion"]))
    print(_row("Requêtes disponibles", len(requests)))
    print(_row("CreateCanvas expose", "oui" if has_create_canvas else "non"))
    print(_row("Go/no-go n°1 -- OBS >= 32.1", "GO" if version_ok else "NO-GO"))
    print("Aitum Vertical : version non vérifiable via obs-websocket, vérifier manuellement dans OBS.")
    return version_ok


def check_canevas(obs: ObsWs) -> bool:
    print("\n=== 2. Canevas ===")
    canvases = obs.request("GetCanvasList")["canvases"]
    for canvas in canvases:
        settings = canvas["canvasVideoSettings"]
        flags = ", ".join(name for name, active in canvas["canvasFlags"].items() if active)
        print(_row("Canevas", canvas["canvasName"]))
        print(_row("  uuid", canvas["canvasUuid"]))
        print(_row("  flags", flags))
        fps = f"{settings['fpsNumerator']}/{settings['fpsDenominator']}"
        print(_row("  resolution", f"{settings['baseWidth']}x{settings['baseHeight']} @ {fps} fps"))

    video_settings = obs.request("GetVideoSettings")
    print(_row("GetVideoSettings", f"{video_settings['baseWidth']}x{video_settings['baseHeight']}"))

    ok = len(canvases) > 0
    print("Note : obs-websocket 5.7.4 annonce un support « partial » des canevas -- lire le dump ci-dessus.")
    print(_row("Go/no-go n°2 -- GetCanvasList exploitable", "GO" if ok else "NO-GO"))
    return ok


_AVCAPTURE_KINDS = ("macos-avcapture", "macos-avcapture-fast", "av_capture_input_v2")


def _obs_avcapture_inputs(obs: ObsWs) -> list[tuple[str, str, str]]:
    inputs = obs.request("GetInputList")["inputs"]
    result = []
    for item in inputs:
        if item["inputKind"] not in _AVCAPTURE_KINDS:
            continue
        settings = obs.request("GetInputSettings", {"inputName": item["inputName"]})["inputSettings"]
        result.append((item["inputName"], item["inputKind"], settings.get("device", "")))
    return result


def check_cameras(obs: ObsWs) -> None:
    print("\n=== 3. Caméras et Center Stage ===")
    try:
        import AVFoundation as AV

        device_types = [
            "AVCaptureDeviceTypeBuiltInWideAngleCamera",
            "AVCaptureDeviceTypeExternal",
            "AVCaptureDeviceTypeContinuityCamera",
        ]
        session = AV.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
            device_types, AV.AVMediaTypeVideo, 0
        )
        devices = list(session.devices())
        global_pref = bool(AV.AVCaptureDevice.isCenterStageEnabled())
    except Exception as exc:
        print(f"Non applicable : AVFoundation indisponible sur cette machine ({exc}).")
        return

    print(_row("Center Stage (préférence globale)", "actif" if global_pref else "inactif"))
    if not devices:
        print("Aucune caméra détectée.")
    active_names = []
    for device in devices:
        active = bool(device.isCenterStageActive())
        if active:
            active_names.append(device.localizedName())
        flag = " *** CENTER STAGE ACTIF ***" if active else ""
        print(_row(device.localizedName(), f"{device.uniqueID()}{flag}"))

    if active_names:
        print(
            f"ATTENTION : Center Stage actif sur {', '.join(active_names)} -- la caméra se recadre "
            "elle-même et fausserait toute mesure du tracker."
        )

    obs_cams = _obs_avcapture_inputs(obs)
    if obs_cams:
        print("Entrées OBS de capture caméra :")
        for name, kind, device_id in obs_cams:
            match = next((d for d in devices if d.uniqueID() == device_id), None)
            label = match.localizedName() if match is not None else "non recoupé"
            print(_row(f"  {name} ({kind})", f"device={device_id} -> {label}"))


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(pct * len(ordered)))
    return ordered[index]


def _timed(fn, n: int) -> list[float]:
    """Call fn n+1 times, discard the first sample (cold-start artefact)."""
    samples = []
    for _ in range(n + 1):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples[1:]


def _report_series(label: str, samples: list[float], ref_median: float | None = None) -> None:
    lo, med = min(samples), statistics.median(samples)
    p90, hi = _percentile(samples, 0.9), max(samples)
    ref = f" (référence médiane {ref_median} ms)" if ref_median is not None else ""
    print(_row(label, f"min={lo:.1f} médiane={med:.1f} p90={p90:.1f} max={hi:.1f} ms{ref}"))


def _report_burst(label: str, fn, n: int = 60) -> None:
    start = time.perf_counter()
    for _ in range(n):
        fn()
    elapsed = time.perf_counter() - start
    print(_row(label, f"{n / elapsed:.0f} req/s ({elapsed * 1000:.0f} ms total, séquentiel, pas concurrent)"))


def check_latences(obs: ObsWs, quick: bool) -> None:
    print("\n=== 4. Latences ===")
    if quick:
        print("Ignoré (--quick).")
        return

    scene_name = obs.request("GetSceneList")["currentProgramSceneName"]
    shot_640 = FrameSource(obs, scene_name, width=640, quality=75)
    shot_1920 = FrameSource(obs, scene_name, width=1920, quality=75)

    _report_series("GetVersion", _timed(lambda: obs.request("GetVersion"), 30), ref_median=0.3)
    _report_series(
        "GetSceneItemList (scène courante)",
        _timed(lambda: obs.request("GetSceneItemList", {"sceneName": scene_name}), 30),
    )
    _report_series("GetSourceScreenshot 640px", _timed(shot_640.grab, 15), ref_median=5.0)
    _report_series("GetSourceScreenshot 1920px", _timed(shot_1920.grab, 15), ref_median=14.0)

    _report_burst("Rafale GetVersion (60 appels séquentiels)", lambda: obs.request("GetVersion"))
    _report_burst("Rafale screenshot 640px (60 appels séquentiels)", shot_640.grab)


def _find_real_fixture(obs: ObsWs) -> tuple[str, int] | None:
    scenes = [s["sceneName"] for s in obs.request("GetSceneList")["scenes"]]
    if _REAL_SCENE not in scenes:
        return None
    items = obs.request("GetSceneItemList", {"sceneName": _REAL_SCENE})["sceneItems"]
    match = next((i for i in items if i["sourceName"] == _REAL_SOURCE), None)
    return (_REAL_SCENE, match["sceneItemId"]) if match else None


def _build_probe_fixture(obs: ObsWs) -> tuple[str, int]:
    obs.request("CreateScene", {"sceneName": _PROBE_PATTERN_SCENE})
    half_w, half_h = 960, 540
    positions = ((0, 0), (half_w, 0), (0, half_h), (half_w, half_h))
    for name, color, (x, y) in zip(_PROBE_QUADRANT_NAMES, _QUADRANT_COLORS, positions):
        item = obs.request(
            "CreateInput",
            {
                "sceneName": _PROBE_PATTERN_SCENE,
                "inputName": name,
                "inputKind": "color_source_v3",
                "inputSettings": {"color": color, "width": half_w, "height": half_h},
            },
        )
        obs.request(
            "SetSceneItemTransform",
            {
                "sceneName": _PROBE_PATTERN_SCENE,
                "sceneItemId": item["sceneItemId"],
                "sceneItemTransform": {"positionX": x, "positionY": y},
            },
        )

    obs.request("CreateScene", {"sceneName": _PROBE_HOST_SCENE})
    host_item = obs.request(
        "CreateSceneItem", {"sceneName": _PROBE_HOST_SCENE, "sourceName": _PROBE_PATTERN_SCENE}
    )
    return _PROBE_HOST_SCENE, host_item["sceneItemId"]


def _safe_remove_scene(obs: ObsWs, name: str) -> None:
    try:
        obs.request("RemoveScene", {"sceneName": name})
    except ObsWsError as exc:
        print(f"Nettoyage : échec suppression scène '{name}' ({exc})")


def _safe_remove_input(obs: ObsWs, name: str) -> None:
    try:
        obs.request("RemoveInput", {"inputName": name})
    except ObsWsError as exc:
        print(f"Nettoyage : échec suppression source '{name}' ({exc})")


def _teardown_probe_fixture(obs: ObsWs) -> None:
    # Reverse creation order: the host scene references the pattern scene,
    # which in turn references the quadrant inputs.
    _safe_remove_scene(obs, _PROBE_HOST_SCENE)
    for name in _PROBE_QUADRANT_NAMES:
        _safe_remove_input(obs, name)
    _safe_remove_scene(obs, _PROBE_PATTERN_SCENE)


def _screenshot_hash(obs: ObsWs, scene_name: str) -> str:
    jpeg = FrameSource(obs, scene_name, width=1920, quality=75).grab()
    return hashlib.sha256(jpeg).hexdigest()


def check_crop(obs: ObsWs) -> bool:
    print("\n=== 5. Go/no-go n°3 -- le crop prend-il effet sans ouvrir le filtre ? ===")
    real = _find_real_fixture(obs)
    created = real is None
    if real is not None:
        host_scene, item_id = real
        print(f"Cas réel utilisé : scène '{host_scene}', source '{_REAL_SOURCE}'.")
    else:
        host_scene, item_id = _build_probe_fixture(obs)
        print("Fixture temporaire créée (scène/source réelles introuvables).")

    try:
        before_hash = _screenshot_hash(obs, host_scene)
        transform = obs.request(
            "GetSceneItemTransform", {"sceneName": host_scene, "sceneItemId": item_id}
        )["sceneItemTransform"]
        source_width = transform["sourceWidth"]
        # ~40% of source width so a real pixel difference is unambiguous.
        crop_right = int(source_width * 0.4) if source_width > 0 else 400

        obs.request(
            "SetSceneItemTransform",
            {
                "sceneName": host_scene,
                "sceneItemId": item_id,
                "sceneItemTransform": {"cropRight": crop_right},
            },
        )
        readback = obs.request(
            "GetSceneItemTransform", {"sceneName": host_scene, "sceneItemId": item_id}
        )["sceneItemTransform"]
        after_hash = _screenshot_hash(obs, host_scene)

        crop_ok = readback["cropRight"] == crop_right
        differ = before_hash != after_hash
        passed = crop_ok and differ

        print(_row("Crop envoyé (cropRight)", crop_right))
        print(_row("Crop relu (cropRight)", readback["cropRight"]))
        print(_row("Hash avant", before_hash[:16]))
        print(_row("Hash après", after_hash[:16]))
        print(_row("Verdict", "LEVÉ" if passed else "ÉCHEC"))
    finally:
        obs.request(
            "SetSceneItemTransform",
            {"sceneName": host_scene, "sceneItemId": item_id, "sceneItemTransform": {"cropRight": 0}},
        )
        if created:
            _teardown_probe_fixture(obs)
    return passed


def check_batch(obs: ObsWs) -> None:
    print("\n=== 6. RequestBatch SERIAL_FRAME ===")
    results = obs.request_batch([("GetVersion", {}), ("GetVideoSettings", {})], execution_type="SERIAL_FRAME")
    ok = len(results) == 2 and "obsVersion" in results[0] and "baseWidth" in results[1]
    print(_row("Résultats appariés", "ok" if ok else "échec"))
    print(_row("obsVersion (batch)", results[0].get("obsVersion")))
    print(_row("baseWidth (batch)", results[1].get("baseWidth")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic go/no-go pour le recadrage hors processus.")
    parser.add_argument("--url", default="ws://127.0.0.1:4455")
    parser.add_argument("--password", default=None)
    parser.add_argument("--quick", action="store_true", help="ignore la série de latences")
    args = parser.parse_args()

    try:
        with ObsWs(url=args.url, password=args.password, timeout=5.0) as obs:
            version_ok = check_environnement(obs)
            canvas_ok = check_canevas(obs)
            check_cameras(obs)
            check_latences(obs, args.quick)
            crop_ok = check_crop(obs)
            check_batch(obs)
    except ObsWsError as exc:
        print(f"Erreur obs-websocket : {exc}", file=sys.stderr)
        return 1

    print("\n=== Résumé go/no-go ===")
    print(_row("1. OBS >= 32.1", "GO" if version_ok else "NO-GO"))
    print(_row("2. GetCanvasList exploitable", "GO" if canvas_ok else "NO-GO"))
    print(_row("3. Crop effectif sans filtre ouvert", "GO" if crop_ok else "NO-GO"))
    return 0 if (version_ok and canvas_ok and crop_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
