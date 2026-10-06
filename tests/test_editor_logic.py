"""Hit-testing, moving and crop-handle math (no window needed)."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo  # noqa: E402

from snapshot_kwin.editor import Editor, hit, text_size, translate  # noqa: E402

RED = (1, 0, 0)


class HitTest(unittest.TestCase):
    def test_line_and_arrow(self):
        o = {"kind": "arrow", "p1": (0, 0), "p2": (100, 0), "color": RED, "width": 4}
        self.assertTrue(hit(o, (50, 3), 6))
        self.assertFalse(hit(o, (50, 30), 6))

    def test_rect_hits_border_not_inside(self):
        o = {"kind": "rect", "p1": (0, 0), "p2": (100, 100), "color": RED, "width": 2}
        self.assertTrue(hit(o, (0, 50), 6))
        self.assertFalse(hit(o, (50, 50), 6))  # drawing inside a rectangle stays possible

    def test_ellipse_border(self):
        o = {"kind": "ellipse", "p1": (0, 0), "p2": (200, 100), "color": RED, "width": 2}
        self.assertTrue(hit(o, (200, 50), 6))
        self.assertFalse(hit(o, (100, 50), 6))

    def test_number_and_text(self):
        n = {"kind": "number", "pos": (50, 50), "n": 1, "color": RED, "radius": 14}
        self.assertTrue(hit(n, (55, 55), 4))
        t = {"kind": "text", "pos": (10, 10), "text": "中文 text", "color": RED, "size": 20}
        t["w"], t["h"] = text_size(t)
        self.assertGreater(t["w"], 40)
        self.assertTrue(hit(t, (10 + t["w"] / 2, 10 + t["h"] / 2), 2))

    def test_translate_every_kind(self):
        objs = [{"kind": "pen", "points": [(0, 0), (1, 1)]}, {"kind": "line", "p1": (0, 0), "p2": (5, 5)},
                {"kind": "text", "pos": (3, 4)}, {"kind": "number", "pos": (1, 1)}]
        for o in objs:
            translate(o, 10, 20)
        self.assertEqual(objs[0]["points"][0], (10, 20))
        self.assertEqual(objs[1]["p2"], (15, 25))
        self.assertEqual(objs[2]["pos"], (13, 24))


def fake_editor(w=800, h=600, ratio=None, crop=(100, 100, 200, 100)):
    ed = types.SimpleNamespace(base=cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h), crop_ratio=ratio, crop_edit=crop)
    ed._fit_ratio = types.MethodType(Editor._fit_ratio, ed)
    return ed


class CropTest(unittest.TestCase):
    def drag(self, ed, mode, dx, dy, anchor=None):
        d = {"mode": mode, "start": ed.crop_edit}
        if anchor:
            d["anchor"] = anchor
        Editor._crop_drag(ed, d, dx, dy)
        return ed.crop_edit

    def test_free_corner(self):
        self.assertEqual(self.drag(fake_editor(), "se", 50, 30), (100, 100, 250, 130))

    def test_move_stays_inside(self):
        x, y, w, h = self.drag(fake_editor(), "move", 10_000, 10_000)
        self.assertEqual((x + w, y + h), (800, 600))

    def test_square_ratio(self):
        x, y, w, h = self.drag(fake_editor(ratio=1.0), "se", 100, 10)
        self.assertAlmostEqual(w, h)

    def test_16_9_edge(self):
        x, y, w, h = self.drag(fake_editor(ratio=16 / 9), "e", 120, 0)
        self.assertAlmostEqual(w / h, 16 / 9, places=5)

    def test_new_rect_clamped(self):
        x, y, w, h = self.drag(fake_editor(), "new", 5000, 5000, anchor=(700, 500))
        self.assertEqual((x, y, w, h), (700, 500, 100, 100))

    def test_fit_ratio_center(self):
        ed = fake_editor(ratio=1.0)
        x, y, w, h = ed._fit_ratio(100, 100, 200, 100, anchor="center")
        self.assertEqual((w, h), (100, 100))
        self.assertEqual((x + w / 2, y + h / 2), (200, 150))


if __name__ == "__main__":
    unittest.main()
