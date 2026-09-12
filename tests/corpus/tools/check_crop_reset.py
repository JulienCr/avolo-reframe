import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.obsws import ObsWs

with ObsWs(url="ws://127.0.0.1:4455", password=None, timeout=5.0) as obs:
    items = obs.request("GetSceneItemList", {"sceneName": "AVOLO Reframe POC"})["sceneItems"]
    for i in items:
        if i["sourceName"] == "RF Cam":
            t = i["sceneItemTransform"]
            print("RF Cam crop after run:", {k: t[k] for k in ("cropLeft","cropTop","cropRight","cropBottom")})
