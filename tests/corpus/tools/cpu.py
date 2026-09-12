import subprocess, sys, time

def cputime(pid):
    """Cumulative CPU seconds for a pid; macOS prints [[DD-]HH:]MM:SS.ss."""
    out = subprocess.run(["ps", "-o", "cputime=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    days, _, rest = out.rpartition("-") if "-" in out else ("0", "", out)
    parts = [float(x) for x in rest.split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs + float(days) * 86400

def sample(pids, seconds):
    a = {k: cputime(v) for k, v in pids.items()}
    t0 = time.monotonic(); time.sleep(seconds); dt = time.monotonic() - t0
    b = {k: cputime(v) for k, v in pids.items()}
    return {k: (b[k] - a[k]) / dt * 100 for k in pids if a[k] is not None and b[k] is not None}

obs = int(subprocess.run(["pgrep", "-x", "OBS"], capture_output=True, text=True).stdout.split()[0])
mode = sys.argv[1]
if mode == "base":
    r = sample({"OBS": obs}, 10)
    print(f"  OBS seul, camera active : {r['OBS']:.0f} % d'un coeur")
else:
    proc = subprocess.Popen(["uv", "run", "python", "-m", "scripts.run", "--duration", "20",
                             "--upper-body", "--fps", "15", "--dead-zone", "0.05",
                             "--dwell-ms", "120", "--ease-ms", "250"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)  # laisser passer le chargement du modele Vision
    r = sample({"OBS": obs, "boucle": proc.pid}, 12)
    print(f"  OBS pendant la boucle   : {r['OBS']:.0f} % d'un coeur")
    print(f"  processus de la boucle  : {r['boucle']:.0f} % d'un coeur")
    proc.wait()
