import subprocess, time

def cputime(pid):
    out = subprocess.run(["ps", "-o", "cputime=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    parts = [float(x) for x in out.split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s

def tree(root):
    """Every pid under root, root included -- uv spawns the real worker as a child."""
    out = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True).stdout
    kids = {}
    for line in out.splitlines():
        pid, ppid = (int(x) for x in line.split())
        kids.setdefault(ppid, []).append(pid)
    seen, stack = [], [root]
    while stack:
        p = stack.pop(); seen.append(p); stack += kids.get(p, [])
    return seen

obs = int(subprocess.run(["pgrep", "-x", "OBS"], capture_output=True, text=True).stdout.split()[0])
proc = subprocess.Popen(["uv", "run", "python", "-m", "scripts.run", "--duration", "22",
                         "--upper-body", "--fps", "15", "--dead-zone", "0.05",
                         "--dwell-ms", "120", "--ease-ms", "250"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)
pids = tree(proc.pid)
a_obs, a_loop = cputime(obs), sum(cputime(p) or 0 for p in pids)
t0 = time.monotonic(); time.sleep(12); dt = time.monotonic() - t0
b_obs, b_loop = cputime(obs), sum(cputime(p) or 0 for p in tree(proc.pid))
print(f"  OBS pendant la boucle  : {(b_obs-a_obs)/dt*100:.0f} % d'un coeur")
print(f"  boucle ({len(pids)} processus)  : {(b_loop-a_loop)/dt*100:.0f} % d'un coeur")
proc.wait()
