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
    ("pen", "画笔", "p"), ("highlight", "荧光笔", "h"), ("line", "直线", "l"), ("arrow", "箭头", "a"),
    ("rect", "矩形", "r"), ("ellipse", "椭圆", "e"), ("text", "文字", "t"), ("number", "编号", "n"),
    ("mosaic", "马赛克", "m"), ("eraser", "橡皮", "x"), ("crop", "裁剪", "c"),
]
# icon names, first available wins (Breeze names first, generic fallbacks after)
ICONS = {
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
DRAG_TOOLS = {"line", "arrow", "rect", "ellipse", "mosaic", "crop"}


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
        return x, y, obj["size"] * 0.6 * max(1, len(obj["text"])), obj["size"] * 1.4
    if k == "number":
        x, y = obj["pos"]
        r = obj["radius"]
        return x - r, y - r, 2 * r, 2 * r
    return 0, 0, 0, 0


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
        self.crop = None
        self.undo_stack, self.redo_stack = [], []
        self.tool = "arrow"
        self.color = COLORS[0][1]
        self.width = 4
        self.next_number = 1
        self.current = None      # object being drawn
        self.drag_from = None    # image coords
        self.view = (1.0, 0.0, 0.0)  # scale, offset x, offset y (widget = img*scale + off)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._build_toolbar())
        self.overlay = Gtk.Overlay()
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self._draw)
        self.overlay.set_child(self.area)
        self.fixed = Gtk.Fixed(can_target=True)
        self.overlay.add_overlay(self.fixed)
        self.fixed.set_can_target(False)
        root.append(self.overlay)
        self.status = Gtk.Label(xalign=0, margin_start=10, margin_end=10, margin_top=4, margin_bottom=6)
        root.append(self.status)
        self.set_child(root)
        self.set_color(self.color)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_begin)
        drag.connect("drag-update", self._on_update)
        drag.connect("drag-end", self._on_end)
        self.area.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close_request)
        self.text_entry = None

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
            r, g, bl = (int(v * 255) for v in rgb)
            css.append(f".skw-swatch-{i} {{ background: rgb({r},{g},{bl}); min-width: 26px; border-radius: 13px; }}")
            bar.append(b)
        css.append(".skw-swatch-on { outline: 3px solid #cdd6f4; outline-offset: 2px; }")
        provider = Gtk.CssProvider()
        provider.load_from_string("\n".join(css))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.width_spin = Gtk.SpinButton.new_with_range(1, 40, 1)
        self.width_spin.set_value(self.width)
        self.width_spin.connect("value-changed", lambda s: setattr(self, "width", int(s.get_value())))
        hover_tip(self.width_spin, "线宽")
        bar.append(self.width_spin)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        for op, tip in (("cw", "顺时针旋转"), ("ccw", "逆时针旋转"), ("fliph", "水平翻转"), ("flipv", "垂直翻转")):
            b = Gtk.Button(icon_name=_icon(op))
            hover_tip(b, tip)
            b.connect("clicked", lambda _b, o=op: self.transform(o))
            bar.append(b)
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        done = Gtk.Button(icon_name=_icon("done"))
        hover_tip(done, "完成:放进剪贴板  ⏎")
        done.add_css_class("suggested-action")
        done.connect("clicked", lambda *_: self.finish())
        bar.append(done)
        return bar

    def set_tool(self, tool):
        self._commit_text()
        self.tool = tool
        if not self.tool_buttons[tool].get_active():
            self.tool_buttons[tool].set_active(True)
        self._update_status()

    def set_color(self, rgb):
        self.color = rgb
        for c, b in getattr(self, "color_buttons", {}).items():
            (b.add_css_class if c == rgb else b.remove_css_class)("skw-swatch-on")
        self._update_status()

    def _update_status(self):
        name = next(lbl for t, lbl, _k in TOOLS if t == self.tool)
        cname = next(n for n, c in COLORS if c == self.color)
        self.status.set_text(f"{name} · {cname} · 线宽 {self.width}    回车 完成并放进剪贴板 · Esc 放弃 · Ctrl/Meta+Z 撤销")

    # ── open / close ─────────────────────────────────────────
    def open_png(self, path):
        surf = cairo.ImageSurface.create_from_png(str(path))
        self.base = surf
        self.objects, self.crop = [], None
        self.undo_stack, self.redo_stack = [], []
        self.next_number = 1
        self.current = None
        self._discard_text()
        self.set_tool(self.tool)
        self.present()
        self.area.queue_draw()

    def _on_close_request(self, *_):
        self._discard_text()
        self.set_visible(False)
        return True  # keep the window for the next open

    def finish(self):
        self._commit_text()
        if self.base is None:
            return
        out = render(self.base, self.objects, self.crop)
        self.on_done(out, self)  # sets the clipboard while we still have focus
        self.set_visible(False)

    # ── history ──────────────────────────────────────────────
    def _snapshot(self):
        return (self.base, copy.deepcopy(self.objects), self.crop, self.next_number)

    def _push_undo(self):
        self.undo_stack.append(self._snapshot())
        self.redo_stack.clear()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self._snapshot())
            self.base, self.objects, self.crop, self.next_number = self.undo_stack.pop()
            self.area.queue_draw()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self._snapshot())
            self.base, self.objects, self.crop, self.next_number = self.redo_stack.pop()
            self.area.queue_draw()

    def transform(self, op):
        self._commit_text()
        if self.base is None:
            return
        self._push_undo()
        # flatten annotations first so they rotate with the image
        flat = render(self.base, self.objects, self.crop)
        self.base = _transform(flat, op)
        self.objects, self.crop = [], None
        self.area.queue_draw()

    # ── coordinates ──────────────────────────────────────────
    def _layout_view(self, width, height):
        bw, bh = self._visible_size()
        scale = min(1.0, (width - 40) / bw, (height - 40) / bh) if bw and bh else 1.0
        ox = (width - bw * scale) / 2
        oy = (height - bh * scale) / 2
        cx, cy = (self.crop[0], self.crop[1]) if self.crop else (0, 0)
        self.view = (scale, ox - cx * scale, oy - cy * scale)

    def _visible_size(self):
        if self.base is None:
            return 0, 0
        if self.crop:
            return self.crop[2], self.crop[3]
        return self.base.get_width(), self.base.get_height()

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
        if self.crop:
            cr.rectangle(*self.crop)
            cr.clip()
        cr.set_source_surface(self.base, 0, 0)
        cr.paint()
        for o in self.objects + ([self.current] if self.current and self.current["kind"] != "crop" else []):
            draw_object(cr, o, self.base)
        cr.restore()
        if self.current and self.current["kind"] == "crop":
            x, y, w, h = _norm(self.current["p1"], self.current["p2"])
            cr.set_source_rgba(0.54, 0.71, 0.98, 1)
            cr.set_line_width(2)
            cr.set_dash([6, 4])
            cr.rectangle(x * s + ox, y * s + oy, w * s, h * s)
            cr.stroke()

    # ── input ────────────────────────────────────────────────
    def _on_begin(self, _g, x, y):
        if self.base is None:
            return
        self._commit_text()
        ix, iy = self._to_img(x, y)
        self.drag_from = (ix, iy)
        t = self.tool
        if t in ("pen", "highlight"):
            self.current = {"kind": t, "points": [(ix, iy)], "color": self.color, "width": self.width}
        elif t in DRAG_TOOLS:
            self.current = {"kind": t, "p1": (ix, iy), "p2": (ix, iy), "color": self.color,
                            "width": self.width, "block": max(8, self.width * 3)}
        elif t == "number":
            self._push_undo()
            self.objects.append({"kind": "number", "pos": (ix, iy), "n": self.next_number,
                                 "color": self.color, "radius": max(14, self.width * 4)})
            self.next_number += 1
        elif t == "text":
            self._start_text(x, y, ix, iy)
        elif t == "eraser":
            self._erase_at(ix, iy)
        self.area.queue_draw()

    def _on_update(self, _g, dx, dy):
        if self.current is None or self.drag_from is None:
            return
        s = self.view[0]
        ix, iy = self.drag_from[0] + dx / s, self.drag_from[1] + dy / s
        if self.current["kind"] in ("pen", "highlight"):
            self.current["points"].append((ix, iy))
        else:
            self.current["p2"] = (ix, iy)
        self.area.queue_draw()

    def _on_end(self, _g, _dx, _dy):
        cur, self.current = self.current, None
        self.drag_from = None
        if cur is None:
            return
        if cur["kind"] == "crop":
            x, y, w, h = _norm(cur["p1"], cur["p2"])
            bw, bh = self.base.get_width(), self.base.get_height()
            x, y = max(0, x), max(0, y)
            w, h = min(bw - x, w), min(bh - y, h)
            if w >= 4 and h >= 4:
                self._push_undo()
                self.crop = (int(x), int(y), int(w), int(h))
        else:
            if cur["kind"] in ("pen", "highlight") and len(cur["points"]) < 2:
                return
            if "p1" in cur and cur["p1"] == cur["p2"]:
                return
            self._push_undo()
            self.objects.append(cur)
        self.area.queue_draw()

    def _erase_at(self, ix, iy):
        for i in range(len(self.objects) - 1, -1, -1):
            x, y, w, h = _bbox(self.objects[i])
            if x <= ix <= x + w and y <= iy <= y + h:
                self._push_undo()
                del self.objects[i]
                return

    # text: a small entry floats over the canvas; Enter/click elsewhere commits
    def _start_text(self, wx, wy, ix, iy):
        self._discard_text()
        entry = Gtk.Entry(width_chars=24, placeholder_text="输入文字，回车确定")
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
            self.objects.append({"kind": "text", "pos": e.img_pos, "text": text,
                                 "color": self.color, "size": max(18, self.width * 5)})
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
            return False  # typing goes to the entry; its Enter commits the text
        if ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.redo() if shift else self.undo()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.finish()
            return True
        if keyval == Gdk.KEY_Escape:
            self._on_close_request()
            return True
        if not ctrl:
            name = Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or ""
            for tid, _label, key in TOOLS:
                if name == key:
                    self.set_tool(tid)
                    return True
        return False
