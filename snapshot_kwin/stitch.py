"""Stitch frames of a scrolling region into one tall image.

Each frame is the same screen rectangle captured after scrolling. Rows are
compared by hash: rows identical at the same position in consecutive frames at
the top/bottom are "sticky" (fixed headers/toolbars) and appear once; in the
scrolling zone between them we vote for the vertical shift that maps the most
rows of the new frame onto the previous one, then append only the newly
revealed rows. Pure Python (no numpy): one hash per row.
"""
import cairo

IGNORE_RIGHT_PX = 24      # scrollbars change while scrolling; ignore them when comparing
MIN_OVERLAP_ROWS = 24     # need this much shared content to trust a shift
MIN_MATCH_RATIO = 0.85    # of the overlapping rows that must match
COMMON_ROW_LIMIT = 6      # rows repeated more often than this (blank lines) do not vote


CONTRAST = 40             # luminance distance from the row mean that counts as "ink"


def row_keys(surface):
    """One key per row describing its *shape*, not its exact colors.

    Translucent windows (terminals over a wallpaper) blend a fixed background
    with scrolling content, so the same text line has different pixel values at
    different heights. We binarize each row against its own mean brightness
    (sampling the green channel of every second pixel) and hash the ink mask,
    which survives smooth background changes.
    """
    surface.flush()
    w, h, stride = surface.get_width(), surface.get_height(), surface.get_stride()
    data = bytes(surface.get_data())
    usable = max(1, w - IGNORE_RIGHT_PX) * 4
    keys = []
    for y in range(h):
        row = data[y * stride + 1: y * stride + usable: 8]  # G of every second pixel (BGRA)
        mean = sum(row) // len(row)
        lo, hi = mean - CONTRAST, mean + CONTRAST
        table = bytes(0 if lo <= v <= hi else 1 for v in range(256))
        keys.append(hash(row.translate(table)))
    return keys


def sticky_rows(prev, new):
    h = len(prev)
    top = 0
    while top < h and prev[top] == new[top]:
        top += 1
    bottom = 0
    while bottom < h - top and prev[h - 1 - bottom] == new[h - 1 - bottom]:
        bottom += 1
    return top, bottom


def find_shift(prev, new):
    """Rows the content moved up between prev and new, or 0 if it did not move / no match.

    Returns (shift, top, bottom) where top/bottom are the sticky row counts.
    """
    h = len(prev)
    top, bottom = sticky_rows(prev, new)
    if top + bottom >= h:  # frames identical: nothing scrolled
        return 0, top, bottom
    # sticky detection can swallow content rows that happen to match by chance;
    # cap it so the scrolling zone keeps a useful height
    top = min(top, h // 3)
    bottom = min(bottom, h // 3)
    zone_lo, zone_hi = top, h - bottom
    positions = {}
    for y in range(zone_lo, zone_hi):
        positions.setdefault(prev[y], []).append(y)
    votes = {}
    for y in range(zone_lo, zone_hi):
        ps = positions.get(new[y])
        if not ps or len(ps) > COMMON_ROW_LIMIT:
            continue
        for p in ps:
            s = p - y
            if s > 0:
                votes[s] = votes.get(s, 0) + 1
    if not votes:
        return 0, top, bottom
    shift = max(votes, key=lambda s: (votes[s], -s))
    overlap = (zone_hi - zone_lo) - shift
    if overlap < MIN_OVERLAP_ROWS:
        return 0, top, bottom
    matched = sum(1 for y in range(zone_lo, zone_lo + overlap) if new[y] == prev[y + shift])
    if matched < MIN_MATCH_RATIO * overlap:
        return 0, top, bottom
    return shift, top, bottom


class Stitcher:
    def __init__(self, max_height=40000):
        self.frames = []    # (surface, shift_from_previous)
        self.keys = None
        self.top = 0
        self.bottom = 0
        self.max_height = max_height

    @property
    def height(self):
        if not self.frames:
            return 0
        return self.frames[0][0].get_height() + sum(s for _f, s in self.frames[1:])

    def add(self, surface):
        """Add a frame. Returns False when nothing new appeared (end reached)."""
        keys = row_keys(surface)
        if self.keys is None:
            self.frames.append((surface, 0))
            self.keys = keys
            return True
        shift, top, bottom = find_shift(self.keys, keys)
        if shift == 0:
            return False
        if len(self.frames) == 1:
            self.top, self.bottom = top, bottom
        else:
            # keep the smallest sticky bands seen, so content is never dropped
            self.top, self.bottom = min(self.top, top), min(self.bottom, bottom)
        self.frames.append((surface, shift))
        self.keys = keys
        return self.height < self.max_height

    def render(self):
        if not self.frames:
            return None
        first = self.frames[0][0]
        w, h = first.get_width(), first.get_height()
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, self.height)
        cr = cairo.Context(out)
        b = self.bottom
        # first frame without its footer
        cr.save()
        cr.rectangle(0, 0, w, h - b)
        cr.clip()
        cr.set_source_surface(first, 0, 0)
        cr.paint()
        cr.restore()
        y = 0
        for frame, shift in self.frames[1:]:
            y += shift
            # only the newly revealed band of this frame: rows [h-b-shift, h-b)
            cr.save()
            cr.rectangle(0, y + h - b - shift, w, shift)
            cr.clip()
            cr.set_source_surface(frame, 0, y)
            cr.paint()
            cr.restore()
        # footer once, from the last frame
        last = self.frames[-1][0]
        cr.save()
        cr.rectangle(0, y + h - b, w, b)
        cr.clip()
        cr.set_source_surface(last, 0, y)
        cr.paint()
        cr.restore()
        return out
