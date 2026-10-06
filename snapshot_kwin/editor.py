"""Image editor, opened from the clipboard history (double-click an image).

Annotations are kept as vector objects in image coordinates until Enter, when
they are rendered onto the image and the result becomes clipboard item #1.

Keys: P pen · H highlighter · L line · A arrow · R rectangle · E ellipse ·
T text · N number · M mosaic · X eraser · C crop · Ctrl/Meta+Z undo ·
Ctrl/Meta+Shift+Z redo · Enter finish · Esc discard.
"""
import copy
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

import cairo  # noqa: E402

EDITOR_TITLE = "snapshot-kwin-editor"

# Catppuccin Mocha accents + white/black
COLORS = [
    ("红", (0.95, 0.55, 0.66)), ("黄", (0.98, 0.89, 0.69)), ("绿", (0.65, 0.89, 0.63)),
    ("蓝", (0.54, 0.71, 0.98)), ("白", (1.0, 1.0, 1.0)), ("黑", (0.07, 0.07, 0.11)),
]
TOOLS = [  # (id, label, key)
    ("select", "选择/移动", "v"), ("pen", "画笔", "p"), ("highlight", "荧光笔", "h"), ("line", "直线", "l"), ("arrow", "箭头", "a"),
    ("rect", "矩形", "r"), ("ellipse", "椭圆", "e"), ("text", "文字", "t"), ("number", "编号", "n"),
    ("mosaic", "马赛克", "m"), ("eraser", "橡皮", "x"), ("crop", "裁剪", "c"),
]
# icon names, first available wins (Breeze names first, generic fallbacks after)
ICONS = {
    "select": ["edit-select", "transform-move", "input-mouse-symbolic"],
    "pen": ["draw-freehand", "draw-brush", "document-edit-symbolic"],
    "highlight": ["draw-highlight", "draw-brush", "format-text-highlight"],
    "line": ["draw-line", "list-remove-symbolic"],
    "arrow": ["draw-arrow", "draw-arrow-forward", "go-next-symbolic"],
    "rect": ["draw-rectangle", "checkbox-symbolic"],
    "ellipse": ["draw-ellipse", "draw-circle", "media-record-symbolic"],
    "text": ["draw-text", "insert-text", "format-text-bold-symbolic"],
    "number": ["draw-number", "format-list-ordered", "zoom-original-symbolic"],
    "mosaic": ["pixelate", "blurfx", "view-grid-symbolic"],
    "eraser": ["draw-eraser", "edit-clear", "edit-delete-symbolic"],
    "crop": ["transform-crop", "edit-cut-symbolic"],
    "cw": ["object-rotate-right", "object-rotate-right-symbolic"],
    "ccw": ["object-rotate-left", "object-rotate-left-symbolic"],
    "fliph": ["object-flip-horizontal", "object-flip-horizontal-symbolic"],
    "flipv": ["object-flip-vertical", "object-flip-vertical-symbolic"],
    "done": ["dialog-ok-apply", "object-select-symbolic"],
}
CROP_RATIOS = [("自由", None), ("1:1", 1.0), ("4:3", 4 / 3), ("3:4", 3 / 4),
               ("16:9", 16 / 9), ("9:16", 9 / 16), ("3:2", 3 / 2)]
HANDLE_PX = 10  # crop handle half-size in screen px
HOVER_MS = 1000  # tooltips appear only after hovering this long


def _icon(name_key):
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    for n in ICONS[name_key]:
        if theme.has_icon(n):
            return n
    return ICONS[name_key][-1]


def hover_tip(widget, text):
    """Show `text` in a small popover after HOVER_MS of hovering; hide on leave/click."""
    pop = Gtk.Popover(autohide=False, has_arrow=True, position=Gtk.PositionType.BOTTOM)
    pop.set_child(Gtk.Label(label=text, margin_start=6, margin_end=6, margin_top=2, margin_bottom=2))
    pop.set_parent(widget)
    state = {"timer": 0}

    def cancel():
        if state["timer"]:
            GLib.source_remove(state["timer"])
            state["timer"] = 0
        pop.popdown()

    def fire():
        state["timer"] = 0
        pop.popup()
        return False

    motion = Gtk.EventControllerMotion()
    motion.connect("enter", lambda *_: (cancel(), state.__setitem__("timer", GLib.timeout_add(HOVER_MS, fire))))
    motion.connect("leave", lambda *_: cancel())
    widget.add_controller(motion)
    click = Gtk.GestureClick()
    click.connect("pressed", lambda *_: cancel())
    widget.add_controller(click)
DRAG_TOOLS = {"line", "arrow", "rect", "ellipse", "mosaic"}


# ── rendering (shared by the canvas and the export) ─────────────────────────
def _rgba(color, alpha=1.0):
    return (*color, alpha)


def draw_object(cr, obj, base):
    k = obj["kind"]
    if k in ("pen", "highlight"):
        pts = obj["points"]
        if len(pts) < 2:
            return
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        alpha = 0.4 if k == "highlight" else 1.0
        cr.set_source_rgba(*_rgba(obj["color"], alpha))
        cr.set_line_width(obj["width"] * (3 if k == "highlight" else 1))
        if k == "highlight":
            cr.set_operator(cairo.OPERATOR_MULTIPLY)
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.stroke()
        cr.restore()
    elif k in ("line", "arrow"):
        (x1, y1), (x2, y2) = obj["p1"], obj["p2"]
        cr.save()
        cr.set_source_rgba(*_rgba(obj["color"]))
        cr.set_line_width(obj["width"])
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(x1, y1)
        cr.line_to(x2, y2)
        cr.stroke()
        if k == "arrow":
            ang = math.atan2(y2 - y1, x2 - x1)
            head = max(12, obj["width"] * 4)
            cr.move_to(x2, y2)
            cr.line_to(x2 - head * math.cos(ang - 0.45), y2 - head * math.sin(ang - 0.45))
            cr.line_to(x2 - head * math.cos(ang + 0.45), y2 - head * math.sin(ang + 0.45))
            cr.close_path()
            cr.fill()
        cr.restore()
    elif k in ("rect", "ellipse"):
        x, y, w, h = _norm(obj["p1"], obj["p2"])
        if w < 1 or h < 1:
            return
        if k == "rect":
            cr.rectangle(x, y, w, h)
        else:
            # the path keeps the scaled ellipse; restoring the matrix keeps the stroke width uniform
            cr.save()
            cr.translate(x + w / 2, y + h / 2)
            cr.scale(w / 2, h / 2)
            cr.new_sub_path()
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
        cr.save()
        cr.set_source_rgba(*_rgba(obj["color"]))
        cr.set_line_width(obj["width"])
        cr.stroke()
        cr.restore()
    elif k == "text":
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(f"Sans Bold {obj['size']}px"))
        layout.set_text(obj["text"], -1)
        cr.save()
        cr.move_to(*obj["pos"])
        # dark outline for readability on any background
        PangoCairo.layout_path(cr, layout)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.set_line_width(max(2, obj["size"] / 8))
        cr.stroke()
        cr.move_to(*obj["pos"])
        cr.set_source_rgba(*_rgba(obj["color"]))
        PangoCairo.show_layout(cr, layout)
        cr.restore()
    elif k == "number":
        x, y = obj["pos"]
        r = obj["radius"]
        cr.save()
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.set_source_rgba(*_rgba(obj["color"]))
        cr.fill()
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(f"Sans Bold {int(r * 1.1)}px"))
        layout.set_text(str(obj["n"]), -1)
        tw, th = layout.get_pixel_size()
        cr.move_to(x - tw / 2, y - th / 2)
        if sum(obj["color"]) > 1.8:  # light badge -> dark digit
            cr.set_source_rgba(0.07, 0.07, 0.11, 1)
        else:
            cr.set_source_rgba(1, 1, 1, 1)
        PangoCairo.show_layout(cr, layout)
        cr.restore()
    elif k == "mosaic":
        x, y, w, h = (int(round(v)) for v in _norm(obj["p1"], obj["p2"]))
        if w < 2 or h < 2:
            return
        block = obj["block"]
        sw, sh = max(1, w // block), max(1, h // block)
        small = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
        sc = cairo.Context(small)
        sc.scale(sw / w, sh / h)
        sc.set_source_surface(base, -x, -y)
        sc.get_source().set_filter(cairo.FILTER_GOOD)
        sc.paint()
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        cr.translate(x, y)
        cr.scale(w / sw, h / sh)
        cr.set_source_surface(small, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        cr.restore()


def _norm(p1, p2):
    x, y = min(p1[0], p2[0]), min(p1[1], p2[1])
    return x, y, abs(p2[0] - p1[0]), abs(p2[1] - p1[1])


def _bbox(obj):
    k = obj["kind"]
    if k in ("pen", "highlight"):
        xs = [p[0] for p in obj["points"]]
        ys = [p[1] for p in obj["points"]]
        pad = obj["width"] * 2
        return min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad
    if k in ("line", "arrow", "rect", "ellipse", "mosaic"):
        x, y, w, h = _norm(obj["p1"], obj["p2"])
        pad = obj.get("width", 4) + 6
        return x - pad, y - pad, w + 2 * pad, h + 2 * pad
    if k == "text":
        x, y = obj["pos"]
        w, h = obj.get("w"), obj.get("h")
        if w is None:
            w, h = text_size(obj)
        return x, y, w, h
    if k == "number":
        x, y = obj["pos"]
        r = obj["radius"]
        return x - r, y - r, 2 * r, 2 * r
    return 0, 0, 0, 0


def _seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def hit(obj, p, tol):
    """True if image point p touches obj (strokes/outlines by distance, solids by area)."""
    k = obj["kind"]
    t = tol + obj.get("width", 0) / 2
    if k in ("pen", "highlight"):
        pts = obj["points"]
        t += obj["width"] if k == "highlight" else 0
        return any(_seg_dist(p, pts[i], pts[i + 1]) <= t for i in range(len(pts) - 1))
    if k in ("line", "arrow"):
        return _seg_dist(p, obj["p1"], obj["p2"]) <= t + (6 if k == "arrow" else 0)
    if k == "rect":
        x, y, w, h = _norm(obj["p1"], obj["p2"])
        inside_outer = x - t <= p[0] <= x + w + t and y - t <= p[1] <= y + h + t
        inside_inner = x + t < p[0] < x + w - t and y + t < p[1] < y + h - t
        return inside_outer and not inside_inner
    if k == "ellipse":
        x, y, w, h = _norm(obj["p1"], obj["p2"])
        a, b = w / 2, h / 2
        if a < 1 or b < 1:
            return False
        r = math.hypot((p[0] - x - a) / a, (p[1] - y - b) / b)
        return abs(r - 1) <= t / min(a, b)
    if k == "number":
        return math.hypot(p[0] - obj["pos"][0], p[1] - obj["pos"][1]) <= obj["radius"] + tol
    x, y, w, h = _bbox(obj)  # text, mosaic
    return x - tol <= p[0] <= x + w + tol and y - tol <= p[1] <= y + h + tol


def translate(obj, dx, dy):
    k = obj["kind"]
    if k in ("pen", "highlight"):
        obj["points"] = [(x + dx, y + dy) for x, y in obj["points"]]
    elif "p1" in obj:
        obj["p1"] = (obj["p1"][0] + dx, obj["p1"][1] + dy)
        obj["p2"] = (obj["p2"][0] + dx, obj["p2"][1] + dy)
    else:
        obj["pos"] = (obj["pos"][0] + dx, obj["pos"][1] + dy)


def text_size(obj):
    """Measured (w, h) of a text object in image px."""
    tmp = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    layout = PangoCairo.create_layout(cairo.Context(tmp))
    layout.set_font_description(Pango.FontDescription.from_string(f"Sans Bold {obj['size']}px"))
    layout.set_text(obj["text"], -1)
    return layout.get_pixel_size()


def render(base, objects, crop=None):
    """Flatten base + objects into a new surface (optionally cropped)."""
    w, h = base.get_width(), base.get_height()
    cx, cy, cw, ch = (0, 0, w, h) if crop is None else crop
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(cw), int(ch))
    cr = cairo.Context(out)
    cr.translate(-cx, -cy)
    cr.set_source_surface(base, 0, 0)
    cr.paint()
    for o in objects:
        draw_object(cr, o, base)
    return out


def _transform(surface, op):
    """Rotate 90° cw/ccw or flip h/v; returns a new surface."""
    w, h = surface.get_width(), surface.get_height()
    nw, nh = (h, w) if op in ("cw", "ccw") else (w, h)
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, nw, nh)
    cr = cairo.Context(out)
    if op == "cw":
        cr.translate(nw, 0)
        cr.rotate(math.pi / 2)
    elif op == "ccw":
        cr.translate(0, nh)
        cr.rotate(-math.pi / 2)
    elif op == "fliph":
        cr.translate(w, 0)
        cr.scale(-1, 1)
    elif op == "flipv":
        cr.translate(0, h)
        cr.scale(1, -1)
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    return out


# ── window ──────────────────────────────────────────────────────────────────
class Editor(Gtk.Window):
    def __init__(self, app, on_done):
        super().__init__(application=app, title=EDITOR_TITLE, default_width=1280, default_height=860)
        self.on_done = on_done
        self.base = None
        self.objects = []
        self.crop = None          # applied crop (x, y, w, h) in image px
        self.crop_edit = None     # pending crop rect while the crop tool is active
        self.crop_ratio = None
        self.undo_stack, self.redo_stack = [], []
        self.tool = "arrow"
        self.color = COLORS[0][1]
        self.width = 4
        self.next_number = 1
        self.current = None       # object being drawn
        self.selected = None      # object shown with a dashed box (move / Delete)
        self.drag = None          # active drag: dict(mode=..., ...)
        self.view = (1.0, 0.0, 0.0)
        self.text_entry = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._build_toolbar())
        self.overlay = Gtk.Overlay()
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.area.set_draw_func(self._draw)
        self.overlay.set_child(self.area)
        self.fixed = Gtk.Fixed()
        self.fixed.set_can_target(False)
        self.overlay.add_overlay(self.fixed)
        root.append(self.overlay)
        self.set_child(root)
        self.set_color(self.color)

        g = Gtk.GestureDrag()
        g.connect("drag-begin", self._on_begin)
        g.connect("drag-update", self._on_update)
        g.connect("drag-end", self._on_end)
        self.area.add_controller(g)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close_request)

    # ── toolbar ──────────────────────────────────────────────
    def _build_toolbar(self):
        bar = Gtk.Box(spacing=4, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        self.tool_buttons = {}
        group = None
        for tid, label, key in TOOLS:
            b = Gtk.ToggleButton(icon_name=_icon(tid))
            hover_tip(b, f"{label}  {key.upper()}")
            if group:
                b.set_group(group)
            group = group or b
            b.connect("toggled", lambda btn, t=tid: btn.get_active() and self.set_tool(t))
            self.tool_buttons[tid] = b
            bar.append(b)
        # crop ratio picker, visible only with the crop tool
        self.ratio_box = Gtk.DropDown.new_from_strings([n for n, _r in CROP_RATIOS])
        self.ratio_box.connect("notify::selected", self._on_ratio)
        hover_tip(self.ratio_box, "裁剪比例")
        self.ratio_box.set_visible(False)
        bar.append(self.ratio_box)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.color_buttons = {}
        css = []
        for i, (name, rgb) in enumerate(COLORS):
            b = Gtk.Button()
            hover_tip(b, f"颜色:{name}")
            b.add_css_class(f"skw-swatch-{i}")
            b.set_size_request(30, 30)
            b.connect("clicked", lambda _b, c=rgb: self.set_color(c))
            self.color_buttons[rgb] = b
            r, gg, bl = (int(v * 255) for v in rgb)
            css.append(f".skw-swatch-{i} {{ background: rgb({r},{gg},{bl}); min-width: 26px; border-radius: 13px; }}")
            bar.append(b)
        css.append(".skw-swatch-on { outline: 3px solid #cdd6f4; outline-offset: 2px; }")
        provider = Gtk.CssProvider()
        provider.load_from_string("\n".join(css))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.width_spin = Gtk.SpinButton.new_with_range(1, 40, 1)
        self.width_spin.set_value(self.width)
        self.width_spin.connect("value-changed", lambda sp: self._set_width(int(sp.get_value())))
        hover_tip(self.width_spin, "线宽")
        bar.append(self.width_spin)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        for op, tip in (("cw", "顺时针旋转"), ("ccw", "逆时针旋转"), ("fliph", "水平翻转"), ("flipv", "垂直翻转")):
            b = Gtk.Button(icon_name=_icon(op))
            hover_tip(b, tip)
            b.connect("clicked", lambda _b, o=op: self.transform(o))
            bar.append(b)
        bar.append(Gtk.Box(hexpand=True))
        done = Gtk.Button(icon_name=_icon("done"))
        hover_tip(done, "完成:放进剪贴板  ⏎")
        done.add_css_class("suggested-action")
        done.connect("clicked", lambda *_: self.finish())
        bar.append(done)
        return bar

    def set_tool(self, tool):
        self._commit_text()
        if self.tool == "crop" and tool != "crop":
            self._apply_crop()
        self.tool = tool
        if tool == "crop" and self.base is not None and self.crop_edit is None:
            self.crop_edit = self.crop or (0, 0, self.base.get_width(), self.base.get_height())
        self.ratio_box.set_visible(tool == "crop")
        if not self.tool_buttons[tool].get_active():
            self.tool_buttons[tool].set_active(True)
        self._update_status()
        self.area.queue_draw()

    def set_color(self, rgb):
        self.color = rgb
        for c, b in self.color_buttons.items():
            (b.add_css_class if c == rgb else b.remove_css_class)("skw-swatch-on")
        if self.selected is not None and "color" in self.selected and self.selected["kind"] != "mosaic":
            self._push_undo()
            self.selected["color"] = rgb
            self.area.queue_draw()
        self._update_status()

    def _set_width(self, w):
        self.width = w
        if self.selected is not None and "width" in self.selected:
            self._push_undo()
            self.selected["width"] = w
            self.area.queue_draw()
        self._update_status()

    def _on_ratio(self, dd, _pspec):
        self.crop_ratio = CROP_RATIOS[dd.get_selected()][1]
        if self.crop_edit and self.crop_ratio:
            x, y, w, h = self.crop_edit
            self.crop_edit = self._fit_ratio(x, y, w, h, anchor="center")
        self.area.queue_draw()

    def _update_status(self):
        pass  # no status line: tools explain themselves via 1 s hover tips

    # ── open / close ─────────────────────────────────────────
    def open_png(self, path):
        self.base = cairo.ImageSurface.create_from_png(str(path))
        self.objects, self.crop, self.crop_edit = [], None, None
        self.undo_stack, self.redo_stack = [], []
        self.next_number = 1
        self.current = self.selected = self.drag = None
        self._discard_text()
        self.set_tool("arrow" if self.tool == "crop" else self.tool)
        self.present()
        self.area.grab_focus()
        self.area.queue_draw()

    def _on_close_request(self, *_):
        self._discard_text()
        self.set_visible(False)
        return True

    def finish(self):
        self._commit_text()
        if self.tool == "crop":
            self._apply_crop()
        if self.base is None:
            return
        out = render(self.base, self.objects, self.crop)
        self.on_done(out, self)
        self.set_visible(False)

    # ── undo ─────────────────────────────────────────────────
    def _snapshot(self):
        return (self.base, copy.deepcopy(self.objects), self.crop, self.next_number)

    def _push_undo(self):
        self.undo_stack.append(self._snapshot())
        self.redo_stack.clear()

    def _restore(self, snap):
        self.base, self.objects, self.crop, self.next_number = snap
        self.selected = None
        self.crop_edit = self.crop if self.tool == "crop" else None
        self.area.queue_draw()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self._snapshot())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self._snapshot())
            self._restore(self.redo_stack.pop())

    def transform(self, op):
        self._commit_text()
        if self.base is None:
            return
        self._push_undo()
        flat = render(self.base, self.objects, self.crop)
        self.base = _transform(flat, op)
        self.objects, self.crop, self.selected = [], None, None
        self.crop_edit = (0, 0, self.base.get_width(), self.base.get_height()) if self.tool == "crop" else None
        self.area.queue_draw()

    # ── crop ─────────────────────────────────────────────────
    def _apply_crop(self):
        if self.crop_edit is None or self.base is None:
            return
        full = (0, 0, self.base.get_width(), self.base.get_height())
        new = tuple(int(round(v)) for v in self.crop_edit)
        self.crop_edit = None
        if new != (self.crop or full) and new[2] >= 4 and new[3] >= 4:
            self._push_undo()
            self.crop = None if new == full else new

    def _fit_ratio(self, x, y, w, h, anchor):
        r = self.crop_ratio
        if not r:
            return (x, y, w, h)
        bw, bh = self.base.get_width(), self.base.get_height()
        if w / max(h, 1) > r:
            nw, nh = h * r, h
        else:
            nw, nh = w, w / r
        nw, nh = min(nw, bw), min(nh, bh)
        if anchor == "center":
            cx, cy = x + w / 2, y + h / 2
            x, y = cx - nw / 2, cy - nh / 2
        x, y = max(0, min(x, bw - nw)), max(0, min(y, bh - nh))
        return (x, y, nw, nh)

    def _crop_handle_at(self, wx, wy):
        if self.crop_edit is None:
            return None
        s, ox, oy = self.view
        x, y, w, h = self.crop_edit
        X, Y, W, H = x * s + ox, y * s + oy, w * s, h * s
        pts = {"nw": (X, Y), "n": (X + W / 2, Y), "ne": (X + W, Y), "e": (X + W, Y + H / 2),
               "se": (X + W, Y + H), "s": (X + W / 2, Y + H), "sw": (X, Y + H), "w": (X, Y + H / 2)}
        for name, (hx, hy) in pts.items():
            if abs(wx - hx) <= HANDLE_PX + 2 and abs(wy - hy) <= HANDLE_PX + 2:
                return name
        if X < wx < X + W and Y < wy < Y + H:
            return "move"
        return None

    def _crop_drag(self, d, dx, dy):
        bw, bh = self.base.get_width(), self.base.get_height()
        x, y, w, h = d["start"]
        mode = d["mode"]
        if mode == "move":
            nx = max(0, min(bw - w, x + dx))
            ny = max(0, min(bh - h, y + dy))
            self.crop_edit = (nx, ny, w, h)
            return
        if mode == "new":
            ax, ay = d["anchor"]
            bx, by = max(0, min(bw, ax + dx)), max(0, min(bh, ay + dy))
            x0, y0, x1, y1 = min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)
        else:
            x0, y0, x1, y1 = x, y, x + w, y + h
            if "w" in mode:
                x0 = max(0, min(x1 - 8, x0 + dx))
            if "e" in mode:
                x1 = min(bw, max(x0 + 8, x1 + dx))
            if "n" in mode:
                y0 = max(0, min(y1 - 8, y0 + dy))
            if "s" in mode:
                y1 = min(bh, max(y0 + 8, y1 + dy))
        nw, nh = x1 - x0, y1 - y0
        r = self.crop_ratio
        if r:
            # keep the dragged corner/edge under the pointer, adapt the other side
            if mode in ("n", "s"):
                nw = nh * r
            elif mode in ("e", "w"):
                nh = nw / r
            elif nw / max(nh, 1) > r:
                nh = nw / r
            else:
                nw = nh * r
            if "w" in mode or (mode == "new" and x0 < d["anchor"][0]):
                x0 = x1 - nw
            if "n" in mode or (mode == "new" and y0 < d["anchor"][1]):
                y0 = y1 - nh
            nw, nh = min(nw, bw), min(nh, bh)
            x0, y0 = max(0, min(x0, bw - nw)), max(0, min(y0, bh - nh))
        self.crop_edit = (x0, y0, nw, nh)

    # ── coordinates ──────────────────────────────────────────
    def _layout_view(self, width, height):
        if self.tool == "crop" or self.crop is None:
            vx, vy, vw, vh = 0, 0, self.base.get_width(), self.base.get_height()
        else:
            vx, vy, vw, vh = self.crop
        scale = min(1.0, (width - 40) / vw, (height - 40) / vh)
        ox = (width - vw * scale) / 2 - vx * scale
        oy = (height - vh * scale) / 2 - vy * scale
        self.view = (scale, ox, oy)

    def _to_img(self, x, y):
        s, ox, oy = self.view
        return (x - ox) / s, (y - oy) / s

    # ── drawing ──────────────────────────────────────────────
    def _draw(self, _area, cr, width, height):
        cr.set_source_rgb(0.12, 0.12, 0.18)
        cr.paint()
        if self.base is None:
            return
        self._layout_view(width, height)
        s, ox, oy = self.view
        cr.save()
        cr.translate(ox, oy)
        cr.scale(s, s)
        if self.crop and self.tool != "crop":
            cr.rectangle(*self.crop)
            cr.clip()
        cr.set_source_surface(self.base, 0, 0)
        cr.paint()
        for o in self.objects + ([self.current] if self.current else []):
            draw_object(cr, o, self.base)
        cr.restore()
        if self.selected is not None and self.selected in self.objects:
            x, y, w, h = _bbox(self.selected)
            cr.set_source_rgba(0.80, 0.84, 0.96, 0.9)
            cr.set_line_width(1)
            cr.set_dash([4, 3])
            cr.rectangle(x * s + ox, y * s + oy, w * s, h * s)
            cr.stroke()
            cr.set_dash([])
        if self.tool == "crop" and self.crop_edit:
            x, y, w, h = self.crop_edit
            X, Y, W, H = x * s + ox, y * s + oy, w * s, h * s
            bw, bh = self.base.get_width() * s, self.base.get_height() * s
            cr.set_source_rgba(0, 0, 0, 0.5)
            cr.rectangle(ox, oy, bw, bh)
            cr.rectangle(X + W, Y, -W, H)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.fill()
            cr.set_fill_rule(cairo.FILL_RULE_WINDING)
            cr.set_source_rgba(0.54, 0.71, 0.98, 1)
            cr.set_line_width(2)
            cr.rectangle(X, Y, W, H)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.35)  # rule-of-thirds guides
            cr.set_line_width(1)
            for i in (1, 2):
                cr.move_to(X + W * i / 3, Y)
                cr.line_to(X + W * i / 3, Y + H)
                cr.move_to(X, Y + H * i / 3)
                cr.line_to(X + W, Y + H * i / 3)
            cr.stroke()
            cr.set_source_rgba(0.54, 0.71, 0.98, 1)
            for hx, hy in ((X, Y), (X + W / 2, Y), (X + W, Y), (X + W, Y + H / 2),
                           (X + W, Y + H), (X + W / 2, Y + H), (X, Y + H), (X, Y + H / 2)):
                cr.rectangle(hx - HANDLE_PX / 2, hy - HANDLE_PX / 2, HANDLE_PX, HANDLE_PX)
            cr.fill()
            label = f"{round(w)} × {round(h)}"
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 10"))
            layout.set_text(label, -1)
            cr.set_source_rgba(0.07, 0.07, 0.11, 0.85)
            tw, th = layout.get_pixel_size()
            cr.rectangle(X + 4, Y + 4, tw + 10, th + 6)
            cr.fill()
            cr.set_source_rgba(0.80, 0.84, 0.96, 1)
            cr.move_to(X + 9, Y + 7)
            PangoCairo.show_layout(cr, layout)

    # ── input ────────────────────────────────────────────────
    def _object_at(self, ix, iy):
        tol = 6 / self.view[0]
        for o in reversed(self.objects):
            if hit(o, (ix, iy), tol):
                return o
        return None

    def _on_begin(self, _g, x, y):
        if self.base is None:
            return
        self.area.grab_focus()
        self._commit_text()
        ix, iy = self._to_img(x, y)
        t = self.tool
        if t == "crop":
            h = self._crop_handle_at(x, y)
            if h is None:
                self.drag = {"mode": "new", "start": self.crop_edit, "anchor": (ix, iy)}
            else:
                self.drag = {"mode": h, "start": self.crop_edit}
            return
        if t == "eraser":
            o = self._object_at(ix, iy)
            if o is not None:
                self._push_undo()
                self.objects.remove(o)
                self.selected = None
            self.area.queue_draw()
            return
        # any other tool: grabbing an existing annotation moves it
        o = self._object_at(ix, iy)
        if o is not None:
            self._push_undo()
            self.selected = o
            self.drag = {"mode": "object", "obj": o, "last": (0.0, 0.0)}
            self.area.queue_draw()
            return
        self.selected = None
        if t == "select":
            self.area.queue_draw()
            return
        self.drag = {"mode": "draw", "from": (ix, iy)}
        if t in ("pen", "highlight"):
            self.current = {"kind": t, "points": [(ix, iy)], "color": self.color, "width": self.width}
        elif t in DRAG_TOOLS:
            self.current = {"kind": t, "p1": (ix, iy), "p2": (ix, iy), "color": self.color,
                            "width": self.width, "block": max(8, self.width * 3)}
        elif t == "number":
            self._push_undo()
            o = {"kind": "number", "pos": (ix, iy), "n": self.next_number,
                 "color": self.color, "radius": max(14, self.width * 4)}
            self.objects.append(o)
            self.selected = o
            self.next_number += 1
            self.drag = None
        elif t == "text":
            self._start_text(x, y, ix, iy)
            self.drag = None
        self.area.queue_draw()

    def _on_update(self, _g, dx, dy):
        d = self.drag
        if d is None:
            return
        s = self.view[0]
        idx, idy = dx / s, dy / s
        if d["mode"] == "object":
            lx, ly = d["last"]
            translate(d["obj"], idx - lx, idy - ly)
            d["last"] = (idx, idy)
        elif d["mode"] == "draw" and self.current is not None:
            fx, fy = d["from"]
            if self.current["kind"] in ("pen", "highlight"):
                self.current["points"].append((fx + idx, fy + idy))
            else:
                self.current["p2"] = (fx + idx, fy + idy)
        elif self.tool == "crop" and d.get("start") is not None:
            self._crop_drag(d, idx, idy)
        self.area.queue_draw()

    def _on_end(self, _g, dx, dy):
        d, self.drag = self.drag, None
        cur, self.current = self.current, None
        if d is None:
            return
        if d["mode"] == "object":
            if abs(dx) < 2 and abs(dy) < 2 and self.undo_stack:
                self.undo_stack.pop()  # a plain click only selects; no undo entry
        elif d["mode"] == "draw" and cur is not None:
            ok = len(cur.get("points", [])) >= 2 if cur["kind"] in ("pen", "highlight") else cur["p1"] != cur["p2"]
            if ok:
                self._push_undo()
                self.objects.append(cur)
                self.selected = cur
        self.area.queue_draw()

    # text: a small entry floats over the canvas; Enter/click elsewhere commits
    def _start_text(self, wx, wy, ix, iy):
        self._discard_text()
        entry = Gtk.Entry(width_chars=24, placeholder_text="输入文字,回车确定")
        entry.img_pos = (ix, iy)
        entry.connect("activate", lambda *_: self._commit_text())
        self.fixed.set_can_target(True)
        self.fixed.put(entry, wx, wy)
        self.text_entry = entry
        GLib.idle_add(entry.grab_focus)

    def _commit_text(self):
        e = self.text_entry
        if e is None:
            return
        text = e.get_text().strip()
        if text:
            self._push_undo()
            o = {"kind": "text", "pos": e.img_pos, "text": text, "color": self.color,
                 "size": max(18, self.width * 5)}
            o["w"], o["h"] = text_size(o)
            self.objects.append(o)
            self.selected = o
        self._discard_text()
        self.area.queue_draw()

    def _discard_text(self):
        if self.text_entry is not None:
            self.fixed.remove(self.text_entry)
            self.text_entry = None
            self.fixed.set_can_target(False)
            self.area.grab_focus()

    def _on_key(self, _ctl, keyval, _code, state):
        ctrl = bool(state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK))
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if self.text_entry is not None:
            if keyval == Gdk.KEY_Escape:
                self._discard_text()
                return True
            return False
        if ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.redo() if shift else self.undo()
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.selected in self.objects:
            self._push_undo()
            self.objects.remove(self.selected)
            self.selected = None
            self.area.queue_draw()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.finish()
            return True
        if keyval == Gdk.KEY_Escape:
            if self.tool == "crop":
                self.crop_edit = None
                self.set_tool("select")
                return True
            self._on_close_request()
            return True
        if not ctrl:
            name = Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or ""
            for tid, _label, key in TOOLS:
                if name == key:
                    self.set_tool(tid)
                    return True
        return False
