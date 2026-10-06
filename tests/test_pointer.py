"""The pointer controller converges under different acceleration curves."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from snapshot_kwin.pointer import PointerMover  # noqa: E402


def simulate(accel, start=(100.0, 900.0), target=(1500, 300), screen=(2560, 1440)):
    pos = list(start)

    def read(cb):
        cb(*pos)

    def move(dx, dy):
        pos[0] = min(max(0, pos[0] + accel(dx)), screen[0] - 1)
        pos[1] = min(max(0, pos[1] + accel(dy)), screen[1] - 1)

    result = {}
    PointerMover(read, move, lambda _ms, f: f()).go(*target, lambda ok, p: result.update(ok=ok, p=p))
    return result


class PointerTest(unittest.TestCase):
    def test_flat(self):
        self.assertTrue(simulate(lambda d: d)["ok"])

    def test_accelerated(self):  # fast motion overshoots
        self.assertTrue(simulate(lambda d: d * (1.6 if abs(d) > 50 else 1.0))["ok"])

    def test_slowed(self):  # big moves undershoot
        self.assertTrue(simulate(lambda d: d * 0.55)["ok"])

    def test_clamped_at_edges(self):
        r = simulate(lambda d: d * 1.3, start=(2559, 0), target=(5, 1430))
        self.assertTrue(r["ok"])


if __name__ == "__main__":
    unittest.main()
