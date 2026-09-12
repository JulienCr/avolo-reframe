import json
import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.obsws import ObsWs

with ObsWs(url="ws://127.0.0.1:4455", password=None, timeout=5.0) as obs:
    v = obs.request("GetVersion")
    top = {k: val for k, val in v.items() if k != "availableRequests"}
    print("=== GetVersion (minus availableRequests) ===")
    print(json.dumps(top, indent=2))
    print("has CreateCanvas:", "CreateCanvas" in v["availableRequests"])
    print("num availableRequests:", len(v["availableRequests"]))

    print("=== GetInputKindList ===")
    ikl = obs.request("GetInputKindList")
    print(json.dumps([k for k in ikl.get("inputKinds", []) if "color" in k], indent=2))
