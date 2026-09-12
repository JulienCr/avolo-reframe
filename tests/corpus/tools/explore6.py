import json
import sys
sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")
from adapters.obsws import ObsWs

with ObsWs(url="ws://127.0.0.1:4455", password=None, timeout=5.0) as obs:
    ikl = obs.request("GetInputKindList")
    print(json.dumps(ikl["inputKinds"], indent=2))
