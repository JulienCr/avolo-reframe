import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.obsws import ObsWs
from scripts.layout import CAM_NAME

with ObsWs(url="ws://127.0.0.1:4455") as obs:
    r = obs.request("GetInputSettings", {"inputName": CAM_NAME})
    print("kind:", r["inputKind"])
    for k, v in sorted(r["inputSettings"].items()):
        print(f"  {k} = {v}")
