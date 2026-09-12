import base64
import hashlib
import json
import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.obsws import ObsWs, ObsWsError

def sha(obs, scene_name):
    r = obs.request("GetSourceScreenshot", {
        "sourceName": scene_name, "imageFormat": "jpg",
        "imageWidth": 1920, "imageCompressionQuality": 75,
    })
    data = r["imageData"]
    _, _, payload = data.partition(",")
    raw = base64.b64decode(payload if payload else data)
    return hashlib.sha256(raw).hexdigest()

with ObsWs(url="ws://127.0.0.1:4455", password=None, timeout=5.0) as obs:
    try:
        # create 4 color inputs as quadrants scene
        obs.request("CreateScene", {"sceneName": "RF Probe Pattern TEST"})
        colors = [0xFFFF0000, 0xFF00FF00, 0xFF0000FF, 0xFFFFFF00]
        names = ["RF Probe C1 TEST", "RF Probe C2 TEST", "RF Probe C3 TEST", "RF Probe C4 TEST"]
        positions = [(0,0),(960,0),(0,540),(960,540)]
        for name, color, pos in zip(names, colors, positions):
            r = obs.request("CreateInput", {
                "sceneName": "RF Probe Pattern TEST",
                "inputName": name,
                "inputKind": "color_source_v3",
                "inputSettings": {"color": color, "width": 960, "height": 540},
            })
            print("created input", name, "sceneItemId=", r.get("sceneItemId"))
            obs.request("SetSceneItemTransform", {
                "sceneName": "RF Probe Pattern TEST",
                "sceneItemId": r["sceneItemId"],
                "sceneItemTransform": {"positionX": pos[0], "positionY": pos[1]},
            })

        obs.request("CreateScene", {"sceneName": "RF Probe Host TEST"})
        r2 = obs.request("CreateSceneItem", {
            "sceneName": "RF Probe Host TEST",
            "sourceName": "RF Probe Pattern TEST",
        })
        item_id = r2["sceneItemId"]
        print("host item id", item_id)

        h1 = sha(obs, "RF Probe Host TEST")
        print("hash before:", h1[:16])

        obs.request("SetSceneItemTransform", {
            "sceneName": "RF Probe Host TEST",
            "sceneItemId": item_id,
            "sceneItemTransform": {"cropRight": 768},
        })
        tf = obs.request("GetSceneItemTransform", {"sceneName": "RF Probe Host TEST", "sceneItemId": item_id})
        print("readback crop:", {k: tf["sceneItemTransform"][k] for k in ("cropLeft","cropTop","cropRight","cropBottom","sourceWidth","sourceHeight")})

        h2 = sha(obs, "RF Probe Host TEST")
        print("hash after:", h2[:16])
        print("differ:", h1 != h2)
    finally:
        obs.request("SetSceneItemTransform", {
            "sceneName": "RF Probe Host TEST",
            "sceneItemId": item_id,
            "sceneItemTransform": {"cropRight": 0},
        })
        for scene in ("RF Probe Host TEST",):
            try:
                obs.request("RemoveScene", {"sceneName": scene})
            except ObsWsError as e:
                print("cleanup err", e)
        for name in names:
            try:
                obs.request("RemoveInput", {"inputName": name})
            except ObsWsError as e:
                print("cleanup err", e)
        try:
            obs.request("RemoveScene", {"sceneName": "RF Probe Pattern TEST"})
        except ObsWsError as e:
            print("cleanup err", e)
