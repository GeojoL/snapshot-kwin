"""Offscreen rendering tests for the editor (no display needed)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo  # noqa: E402

from snapshot_kwin.editor import _transform, render  # noqa: E402

RED = (0.95, 0.55, 0.66)


def checker(w=400, h=300):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(s)
    for y in range(0, h, 20):
        for x in range(0, w, 20):
            v = 0.85 if (x // 20 + y // 20) % 2 else 0.35
            cr.set_source_rgb(v, v * 0.9, v * 0.8)
            cr.rectangle(x, y, 20, 20)
            cr.fill()
    return s


def px(s, x, y):
    s.flush()
    stride = s.get_stride()
    b = bytes(s.get_data())[y * stride + x * 4: y * stride + x * 4 + 4]
    return b[2], b[1], b[0], b[3]  # BGRA -> RGBA


ALL = [
    {"kind": "pen", "points": [(20, 20), (60, 40), (100, 30)], "color": RED, "width": 4},
    {"kind": "highlight", "points": [(20, 80), (180, 80)], "color": (0.98, 0.89, 0.69), "width": 6},
    {"kind": "line", "p1": (20, 120), "p2": (180, 120), "color": RED, "width": 3},
    {"kind": "arrow", "p1": (20, 160), "p2": (180, 200), "color": RED, "width": 4},
    {"kind": "rect", "p1": (220, 20), "p2": (380, 90), "color": (0.54, 0.71, 0.98), "width": 3},
    {"kind": "ellipse", "p1": (220, 110), "p2": (380, 180), "color": (0.65, 0.89, 0.63), "width": 3},
    {"kind": "text", "pos": (220, 200), "text": "中文 Text", "color": (1, 1, 1), "size": 22},
    {"kind": "number", "pos": (60, 250), "n": 3, "color": RED, "radius": 16},
    {"kind": "mosaic", "p1": (240, 240), "p2": (380, 290), "color": RED, "width": 4, "block": 12},
]


class RenderTest(unittest.TestCase):
    def test_all_objects_render_and_change_pixels(self):
        base = checker()
        out = render(base, ALL)
        self.assertEqual((out.get_width(), out.get_height()), (400, 300))
        self.assertNotEqual(px(out, 40, 30)[:3], px(base, 40, 30)[:3])    # pen stroke
        self.assertNotEqual(px(out, 300, 20)[:3], px(base, 300, 20)[:3])   # rect edge
        self.assertNotEqual(px(out, 60, 250)[:3], px(base, 60, 250)[:3])   # number badge
        # mosaic: a 12px block is uniform
        self.assertEqual(px(out, 243, 243), px(out, 249, 249))
        if os.environ.get("SNAPSHOT_KWIN_DUMP"):
            out.write_to_png(os.environ["SNAPSHOT_KWIN_DUMP"])

    def test_crop(self):
        out = render(checker(), [], crop=(10, 20, 100, 50))
        self.assertEqual((out.get_width(), out.get_height()), (100, 50))

    def test_rotate_and_flip(self):
        base = checker(400, 300)
        self.assertEqual((_transform(base, "cw").get_width(), _transform(base, "cw").get_height()), (300, 400))
        f = _transform(base, "fliph")
        self.assertEqual(px(f, 0, 0), px(base, 399, 0))

    def test_png_roundtrip(self):
        out = render(checker(), ALL[:3])
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            out.write_to_png(f.name)
            back = cairo.ImageSurface.create_from_png(f.name)
            self.assertEqual(back.get_width(), 400)


if __name__ == "__main__":
    unittest.main()
