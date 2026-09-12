import json
import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.obsws import ObsWs

with ObsWs(url="ws://127.0.0.1:4455", password=None, timeout=5.0) as obs:
    obs.request("CreateScene", {"sceneName": "RF Probe TmpCheck"})
    r = obs.request("CreateInput", {
        "sceneName": "RF Probe TmpCheck", "inputName": "RF Probe TmpInput",
        "inputKind": "color_source_v3", "inputSettings": {"color": 0xFFFF0000, "width": 100, "height": 100},
    })
    sil = obs.request("GetSceneItemList", {"sceneName": "RF Probe TmpCheck"})
    print(json.dumps(sil, indent=2))
    obs.request("RemoveScene", {"sceneName": "RF Probe TmpCheck"})
    obs.request("RemoveInput", {"inputName": "RF Probe TmpInput"})
