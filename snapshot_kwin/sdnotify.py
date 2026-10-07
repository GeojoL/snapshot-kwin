"""Minimal sd_notify(3) client (no python-systemd dependency).

Lets systemd know the daemon is ready (Type=notify) and still alive
(WatchdogSec=): if the main loop hangs, the pings stop and systemd restarts it.
Outside systemd (tests, manual runs) every call is a no-op.
"""
import os
import socket


def notify(state: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr[0] == "@":  # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.connect(addr)
            s.sendall(state.encode())
        return True
    except OSError:
        return False


def watchdog_interval_s():
    """Seconds between WATCHDOG=1 pings (a third of WatchdogSec), or None."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return None
    pid = os.environ.get("WATCHDOG_PID")
    if pid and pid.isdigit() and int(pid) != os.getpid():
        return None
    return max(1, int(usec) // 3_000_000)
