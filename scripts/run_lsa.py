"""Supervisor for the LSA vertical reframe: one camera loop per scripts.layout_lsa.CAMERAS
entry, plus the director, in one process with one Ctrl-C.

Run as: uv run python -m scripts.run_lsa
"""

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from scripts.layout_lsa import CAMERAS

DEFAULT_CONFIG_PATH = Path("reframe.lsa.toml")
# A loop's TensorRT engine load takes on the order of ten seconds; give it
# room to unwind cleanly before this supervisor kills it.
STOP_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 0.5


def camera_argv(key: str, config: Path, live_key: str, extra: list[str]) -> list[str]:
    """Child command line for one camera's scripts.run loop.

    sys.executable -m scripts.run, not uv run: the supervisor already runs
    inside the target environment, so re-resolving it per child is wasted work.
    """
    argv = [sys.executable, "-m", "scripts.run", "--cam", key, "--config", str(config)]
    if key != live_key:
        argv.append("--no-live")
    return argv + extra


def director_argv(config: Path, extra: list[str]) -> list[str]:
    """Child command line for scripts.director."""
    return [sys.executable, "-m", "scripts.director", "--config", str(config)] + extra


def parse_cams(value: str | None) -> tuple[str, ...]:
    """Camera keys to launch, validated against CAMERAS; None means all of them."""
    if value is None:
        return tuple(CAMERAS)
    keys = tuple(k.strip() for k in value.split(","))
    unknown = [k for k in keys if k not in CAMERAS]
    if unknown:
        valid = ", ".join(CAMERAS)
        raise ValueError(f"Clé(s) inconnue(s) dans --cams : {', '.join(unknown)} (valides : {valid}).")
    return keys


def resolve_live(value: str | None) -> str:
    """The --live key, validated against CAMERAS; None defaults to its first entry."""
    key = value if value is not None else next(iter(CAMERAS))
    if key not in CAMERAS:
        valid = ", ".join(CAMERAS)
        raise ValueError(f"Caméra inconnue pour --live : « {key} » (valides : {valid}).")
    return key


def split_extra_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on a literal "--": (this program's own args, args passed to every child)."""
    if "--" in argv:
        idx = argv.index("--")
        return argv[:idx], argv[idx + 1:]
    return argv, []


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    own, extra = split_extra_args(argv)
    parser = argparse.ArgumentParser(
        description="Lance les quatre boucles LSA et le chef de pupitre en un seul process, un seul Ctrl-C."
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Profil TOML passé à chaque enfant."
    )
    parser.add_argument(
        "--live", default=None,
        help="Caméra à l'antenne au démarrage (défaut : la première de scripts.layout_lsa.CAMERAS).",
    )
    parser.add_argument(
        "--cams", default=None, help="Sous-ensemble de caméras à lancer, séparées par des virgules (défaut : toutes)."
    )
    parser.add_argument("--no-director", action="store_true", help="Ne lance pas le chef de pupitre.")
    return parser.parse_args(own), extra


class Child:
    """One supervised subprocess: its Popen handle and the thread reading its output."""

    def __init__(self, key: str, argv: list[str]) -> None:
        self.key = key
        self.reported = False
        # PYTHONUNBUFFERED rather than a child-side "-u": camera_argv and
        # director_argv stay pure argv builders, and tests pin their shape.
        child_env = os.environ | {"PYTHONUNBUFFERED": "1"}
        self.process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=child_env,
        )
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        """Prefix and print each line as it arrives; a per-child thread keeps
        lines from interleaving mid-line across children.
        """
        stdout = self.process.stdout
        if stdout is None:
            return
        for line in iter(stdout.readline, ""):
            print(f"[{self.key}] {line.rstrip(chr(10))}")
        stdout.close()


def start_children(
    children: list[Child],
    cams: tuple[str, ...],
    config: Path,
    live_key: str,
    extra: list[str],
    with_director: bool,
) -> None:
    """Append started children to `children` in place.

    Appending as each Popen succeeds, rather than building a separate list and
    returning it, means a Popen failure partway through still leaves the
    already-started children reachable for main()'s finally: stop_children.
    """
    for key in cams:
        children.append(Child(key, camera_argv(key, config, live_key, extra)))
    if with_director:
        children.append(Child("director", director_argv(config, extra)))


def stop_children(children: list[Child]) -> None:
    """Ask every still-running child to stop, wait with a guard delay, kill stragglers.

    Idempotent: Ctrl-C in a Windows console reaches the whole process group, so
    a child may already be exiting on its own KeyboardInterrupt when this runs.
    An orphaned child keeps its control port bound (SO_REUSEADDR is off on
    win32), so nothing here is allowed to return before every child is gone.
    """
    for child in children:
        if child.process.poll() is None:
            child.process.terminate()
    deadline = time.monotonic() + STOP_TIMEOUT_S
    for child in children:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            child.process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"[{child.key}] ne répond pas, arrêt forcé.")
            child.process.kill()
            child.process.wait()
        child.thread.join(timeout=1.0)


def main() -> None:
    # Line-buffered and write-through: this process's own prints must reach a
    # redirected log file as they happen, not only at a clean exit.
    sys.stdout.reconfigure(line_buffering=True, write_through=True)

    args, extra = parse_args(sys.argv[1:])
    try:
        cams = parse_cams(args.cams)
        live_key = resolve_live(args.live)
    except ValueError as exc:
        print(str(exc))
        sys.exit(2)
    if live_key not in cams:
        print(f"Attention : « {live_key} » (--live) n'est pas dans --cams, aucune caméra lancée ne sera à l'antenne.")

    children: list[Child] = []
    failed = False
    interrupted = False
    try:
        start_children(children, cams, args.config, live_key, extra, with_director=not args.no_director)
        while True:
            time.sleep(POLL_INTERVAL_S)
            alive = False
            for child in children:
                code = child.process.poll()
                if code is None:
                    alive = True
                    continue
                if not child.reported:
                    child.reported = True
                    print(f"[{child.key}] processus terminé (code {code}).")
                    if code != 0:
                        failed = True
            if not alive:
                break
    except KeyboardInterrupt:
        interrupted = True
    except Exception as exc:
        print(f"[superviseur] arrêt sur exception inattendue : {exc!r}")
        # finally below stops every started child before this propagates.
        raise
    finally:
        stop_children(children)

    sys.exit(0 if interrupted else (1 if failed else 0))


if __name__ == "__main__":
    main()
