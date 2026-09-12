import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.obsws import ObsWs

with ObsWs(url="ws://127.0.0.1:4455", password=None, timeout=5.0) as obs:
    sl = obs.request("GetSceneList")
    print("scenes:", [s["sceneName"] for s in sl["scenes"]])
    il = obs.request("GetInputList")
    print("inputs:", [i["inputName"] for i in il["inputs"]])
