"""Stitching tests with synthetic scrolling frames (no display needed)."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo  # noqa: E402

from snapshot_kwin.stitch import Stitcher, find_shift, row_keys  # noqa: E402


def page(height=3000, width=300, seed=1):
    """A long 'document': every row has a distinct-ish pattern (like text lines)."""
    rnd = random.Random(seed)
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(s)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    for y in range(0, height, 3):
        cr.set_source_rgb(rnd.random(), rnd.random(), rnd.random())
        cr.rectangle(rnd.randint(0, width // 2), y, rnd.randint(10, width // 2), 2)
        cr.fill()
    return s


def view(doc, scroll, h=400, header=0, footer=0):
    """What a window of height h shows at `scroll`, with optional sticky header/footer."""
    w = doc.get_width()
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(s)
    cr.set_source_surface(doc, 0, header - scroll)
    cr.paint()
    for y0, hh, col in ((0, header, (0.2, 0.3, 0.8)), (h - footer, footer, (0.8, 0.3, 0.2))):
        if hh:
            cr.set_source_rgb(*col)
            cr.rectangle(0, y0, w, hh)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(10, y0 + hh // 3, 80, hh // 3)  # some static detail
            cr.fill()
    return s


def same_pixels(a, b, y0=0, rows=None):
    a.flush(), b.flush()
    rows = rows if rows is not None else a.get_height()
    da, db = bytes(a.get_data()), bytes(b.get_data())
    sa, sb = a.get_stride(), b.get_stride()
    w = a.get_width() * 4
    return all(da[(y0 + y) * sa:(y0 + y) * sa + w] == db[y * sb:y * sb + w] for y in range(rows))


def translucent_view(doc, scroll, h=400):
    """Content scrolls, a vertical gradient 'wallpaper' stays put and shows through (alpha 0.85)."""
    w = doc.get_width()
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(s)
    g = cairo.LinearGradient(0, 0, 0, h)
    g.add_color_stop_rgb(0, 0.10, 0.12, 0.30)
    g.add_color_stop_rgb(1, 0.30, 0.10, 0.20)
    cr.set_source(g)
    cr.paint()
    cr.set_source_surface(doc, 0, -scroll)
    cr.paint_with_alpha(0.85)
    return s


def text_page(height=1600, width=300, seed=2):
    """Dark page with light 'text' strokes, like a terminal."""
    rnd = random.Random(seed)
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(s)
    cr.set_source_rgb(0.05, 0.05, 0.08)
    cr.paint()
    cr.set_source_rgb(0.9, 0.9, 0.9)
    for y in range(4, height, 18):
        x = 4
        while x < width - 40:
            ww = rnd.randint(4, 30)
            cr.rectangle(x, y, ww, rnd.randint(6, 12))
            x += ww + rnd.randint(4, 12)
        cr.fill()
    return s


class StitchTest(unittest.TestCase):
    def test_translucent_terminal_over_gradient(self):
        doc = text_page()
        st = Stitcher()
        scroll, maxs = 0, 1600 - 400
        frames = 0
        while True:
            frames += 1
            if not st.add(translucent_view(doc, min(scroll, maxs))):
                break
            scroll += 200
        self.assertEqual(st.render().get_height(), 1600)

    def test_find_shift_plain(self):
        doc = page()
        s, top, bottom = find_shift(row_keys(view(doc, 0)), row_keys(view(doc, 150)))
        self.assertEqual((s, top, bottom), (150, 0, 0))

    def test_identical_frames_mean_end(self):
        doc = page()
        k = row_keys(view(doc, 100))
        self.assertEqual(find_shift(k, k)[0], 0)

    def test_stitch_reconstructs_document(self):
        doc = page(height=1600)
        st = Stitcher()
        scroll = 0
        while True:
            more = st.add(view(doc, min(scroll, 1600 - 400)))
            if not more:
                break
            scroll += 170
        out = st.render()
        self.assertEqual(out.get_height(), 1600)
        self.assertTrue(same_pixels(doc, out, 0, 1600))

    def test_sticky_header_and_footer_once(self):
        doc = page(height=1400)
        H, head, foot = 400, 50, 40
        st = Stitcher()
        scroll, maxs = 0, 1400 - (H - head - foot)
        while True:
            if not st.add(view(doc, min(scroll, maxs), H, head, foot)):
                break
            scroll += 120
        out = st.render()
        # header + whole document + footer, nothing duplicated
        self.assertEqual(out.get_height(), head + 1400 + foot)
        first = view(doc, 0, H, head, foot)
        self.assertTrue(same_pixels(first, out, 0, head))  # header at the top
        last = view(doc, maxs, H, head, foot)
        lw = cairo.ImageSurface(cairo.FORMAT_ARGB32, last.get_width(), foot)
        cr = cairo.Context(lw)
        cr.set_source_surface(last, 0, -(H - foot))
        cr.paint()
        self.assertTrue(same_pixels(out, lw, out.get_height() - foot, foot))  # footer at the bottom


if __name__ == "__main__":
    unittest.main()
