"""Move the pointer to an exact spot with relative uinput motion.

Absolute positioning through uinput is unreliable because pointer acceleration
scales relative motion. We read the real cursor position from KWin, move by the
remaining difference and repeat until within tolerance — this converges for any
monotonic acceleration curve. Used by long capture so the wheel scrolls the pane
under the *center* of the selection, then puts the pointer back.
"""
import os
import subprocess

TOLERANCE_PX = 2
MAX_ROUNDS = 8


class PointerMover:
    """Async controller: read(cb) must call cb(x, y); move(dx, dy) sends relative motion."""

    def __init__(self, read, move, schedule, log=lambda *_: None):
        self.read, self.move, self.schedule, self.log = read, move, schedule, log

    def go(self, tx, ty, done):
        # per-axis gain estimate: actual motion / commanded motion (acceleration)
        st = {"round": 0, "gain": [1.0, 1.0], "last": None, "cmd": None}

        def on_pos(x, y):
            if st["last"] is not None:
                for i, (moved, cmd) in enumerate(zip((x - st["last"][0], y - st["last"][1]), st["cmd"])):
                    if abs(cmd) >= 3 and moved * cmd > 0:
                        st["gain"][i] = min(5.0, max(0.2, moved / cmd))
            ex, ey = tx - x, ty - y
            if abs(ex) <= TOLERANCE_PX and abs(ey) <= TOLERANCE_PX:
                done(True, (x, y))
                return
            if st["round"] >= MAX_ROUNDS:
                self.log(f"pointer did not converge: at ({x:.0f},{y:.0f}) target ({tx},{ty})")
                done(False, (x, y))
                return
            st["round"] += 1
            cmd = (round(ex / st["gain"][0]), round(ey / st["gain"][1]))
            st["last"], st["cmd"] = (x, y), cmd
            self.move(*cmd)
            self.schedule(70, lambda: self.read(on_pos))

        self.read(on_pos)


def ydotool_move(dx, dy):
    sock = os.environ.get("YDOTOOL_SOCKET") or os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), ".ydotool_socket")
    try:
        subprocess.run(["ydotool", "mousemove", "-x", str(dx), "-y", str(dy)],
                       env=dict(os.environ, YDOTOOL_SOCKET=sock), timeout=2, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass
