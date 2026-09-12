import itertools
import sys

sys.path.insert(0, "/Users/julien.cruau/dev2/avolo-reframe")

from adapters.frames import FrameSource
from adapters.obsws import ObsWsError

original_grab = FrameSource.grab
call_count = itertools.count()
FAIL_AT = {5, 6}


def flaky_grab(self):
    n = next(call_count)
    if n in FAIL_AT:
        raise ObsWsError(f"simulated multi-second stall (test-injected, call {n})")
    return original_grab(self)


FrameSource.grab = flaky_grab

sys.argv = [
    "run.py",
    "--duration", "8",
    "--upper-body",
    "--log", "/private/tmp/claude-502/-Users-julien-cruau-dev2-avolo-reframe/4df7d600-192d-4470-b571-5d49aefccb3f/scratchpad/run_resilience.jsonl",
]

import scripts.run as run_mod

run_mod.main()
print("total grab() calls attempted:", next(call_count))
