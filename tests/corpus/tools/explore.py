import json
import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.obsws import ObsWs

with ObsWs(url="ws://127.0.0.1:4455", password=None, timeout=5.0) as obs:
    v = obs.request("GetVersion")
    print("=== GetVersion ===")
    print(json.dumps(v, indent=2)[:3000])

    print("=== GetCanvasList ===")
    try:
        c = obs.request("GetCanvasList")
        print(json.dumps(c, indent=2)[:3000])
    except Exception as e:
        print("ERROR", e)

    print("=== GetVideoSettings ===")
    vs = obs.request("GetVideoSettings")
    print(json.dumps(vs, indent=2))

    print("=== GetSceneList ===")
    sl = obs.request("GetSceneList")
    print(json.dumps(sl, indent=2)[:2000])

    print("=== GetInputList ===")
    il = obs.request("GetInputList")
    print(json.dumps([i.get("inputName") for i in il.get("inputs", [])], indent=2))

    cur_scene = sl.get("currentProgramSceneName")
    print("current scene:", cur_scene, sl.get("currentProgramSceneUuid"))

    print("=== GetSceneItemList (current) ===")
    sil = obs.request("GetSceneItemList", {"sceneName": cur_scene})
    print(json.dumps(sil, indent=2)[:3000])
