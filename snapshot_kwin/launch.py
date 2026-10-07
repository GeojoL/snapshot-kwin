"""Entry point of the protected (system-level) service.

The system unit runs as the desktop user but outside the user's session, so it
has none of the session's environment. Wait until the user's Plasma Wayland
session is up (session bus present, KWin on it, a Wayland socket), adopt the
user manager's environment, then exec the daemon in this same process, so
systemd keeps tracking one main PID (Type=notify, watchdog).

In Game Mode (gamescope) there is no KWin, so it keeps waiting, like the
per-user unit that is only wanted by plasma-workspace.target.
"""
import json
import os
import subprocess
import sys
import time

from . import sdnotify

KEEP = ("NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE",
        "INVOCATION_ID", "JOURNAL_STREAM", "HOME", "USER", "LOGNAME")


def session_env():
    run = f"/run/user/{os.getuid()}"
    bus_addr = f"unix:path={run}/bus"
    if not os.path.exists(f"{run}/bus"):
        return None
    base = dict(os.environ, XDG_RUNTIME_DIR=run, DBUS_SESSION_BUS_ADDRESS=bus_addr)

    def busctl(*args):
        return subprocess.run(["busctl", "--user", *args], env=base, capture_output=True, text=True, timeout=5)

    try:
        if busctl("status", "org.kde.KWin").returncode != 0:
            return None
        r = busctl("--json=short", "get-property", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                   "org.freedesktop.systemd1.Manager", "Environment")
        if r.returncode != 0:
            return None
        env = dict(e.split("=", 1) for e in json.loads(r.stdout)["data"] if "=" in e)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return None
    wl = env.get("WAYLAND_DISPLAY")
    if not wl or not os.path.exists(os.path.join(run, wl)):
        return None
    env.update(XDG_RUNTIME_DIR=run, DBUS_SESSION_BUS_ADDRESS=bus_addr)
    env.update({k: os.environ[k] for k in KEEP if k in os.environ})
    return env


def main():
    announced = False
    while True:
        env = session_env()
        if env is not None:
            break
        if not announced:
            print("snapshot-kwin: waiting for the Plasma Wayland session", file=sys.stderr, flush=True)
            sdnotify.notify("STATUS=waiting for the Plasma Wayland session")
            announced = True
        sdnotify.notify("WATCHDOG=1")
        time.sleep(2)
    os.execve(sys.executable, [sys.executable, "-m", "snapshot_kwin.app"], env)


if __name__ == "__main__":
    main()
