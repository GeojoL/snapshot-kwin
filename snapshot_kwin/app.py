"""snapshot-kwin daemon: instant region / window capture on KDE Plasma (Wayland).

Workflow (see docs/design-notes.zh.md):
  hotkey -> frozen fullscreen overlay -> drag = region, click = whole window
  -> PNG goes to the clipboard and to the history dir. No editor pops up.

The process stays resident and keeps the overlay window built, so a capture only
costs the KWin grab (~60 ms) plus one frame.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

import cairo  # noqa: E402

import dbus  # noqa: E402

from .capture import CaptureError, KWinCapture  # noqa: E402
from .clipboard import History, Picker  # noqa: E402
from .editor import Editor  # noqa: E402
from .stitch import Stitcher  # noqa: E402


def dbus_iface(obj, name):
    return dbus.Interface(obj, name)

APP_ID = "io.github.geojol.SnapshotKwin"
OBJECT_PATH = "/io/github/geojol/SnapshotKwin"
OVERLAY_TITLE = "snapshot-kwin-overlay"
CLICK_SLOP = 4  # logical px: below this a drag counts as a click

DBUS_XML = f"""
<node>
  <interface name="{APP_ID}">
    <method name="UpdateWindows"><arg type="s" name="json" direction="in"/></method>
    <method name="Capture"/>
    <method name="ShowHistory"/>
    <method name="TestHideHistory"/>
    <method name="TestEditLatest"/>
    <method name="TestEditorArrowAndFinish"/>
    <method name="TestEditorTool"><arg type="s" name="tool"/></method>
    <!-- long capture with arrow-key scrolling of the focused test window (no pointer use) -->
    <method name="TestLong"><arg type="i" name="x"/><arg type="i" name="y"/><arg type="i" name="w"/><arg type="i" name="h"/><arg type="i" name="down_keys"/></method>
    <!-- test hooks: drive the overlay without moving the user's pointer -->
    <method name="TestSelect"><arg type="i" name="x"/><arg type="i" name="y"/><arg type="i" name="w"/><arg type="i" name="h"/></method>
    <method name="TestClick"><arg type="i" name="x"/><arg type="i" name="y"/></method>
  </interface>
</node>
"""


def _log(msg):
    print(f"[snapshot-kwin {time.time() * 1000:.0f}] {msg}", file=sys.stderr, flush=True)


def history_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    d = Path(base) / "snapshot-kwin" / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Overlay(Gtk.Window):
    def __init__(self, app):
        super().__init__(application=app, title=OVERLAY_TITLE, decorated=False)
        self.app = app
        self.surface = None          # frozen screenshot, native pixels
        self.windows = []            # [{x,y,w,h,...}] logical px, bottom -> top
        self.drag_start = None
        self.drag_rect = None        # (x, y, w, h) logical px
        self.hover = None            # window dict under pointer
        self.trigger_ms = None
        self.long_mode = False
        self._first_frame_logged = True

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self._draw)
        self.area.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        self.set_child(self.area)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.area.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.area.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self.area.add_tick_callback(self._on_tick)

    # ── lifecycle ────────────────────────────────────────────
    def show_capture(self, surface, windows, trigger_ms):
        self.surface = surface
        self.windows = windows
        self.drag_start = self.drag_rect = self.hover = None
        self.long_mode = False
        self.trigger_ms = trigger_ms
        self._first_frame_logged = False
        self.fullscreen()
        self.present()
        self.area.queue_draw()

    def dismiss(self):
        self.set_visible(False)
        self.surface = None

    def _on_tick(self, _widget, _clock):
        if not self._first_frame_logged and self.surface is not None and self.get_mapped():
            self._first_frame_logged = True
            if self.trigger_ms is not None:
                _log(f"overlay-visible latency_ms={time.time() * 1000 - self.trigger_ms:.0f}")
        return GLib.SOURCE_CONTINUE

    # ── geometry helpers ─────────────────────────────────────
    def _scale(self):
        w = self.area.get_width() or 1
        return self.surface.get_width() / w

    def _window_at(self, x, y):
        for win in reversed(self.windows):  # topmost first
            if win["x"] <= x < win["x"] + win["w"] and win["y"] <= y < win["y"] + win["h"]:
                return win
        return None

    # ── drawing ──────────────────────────────────────────────
    def _draw(self, _area, cr, width, height):
        if self.surface is None:
            return
        s = self._scale()
        cr.save()
        cr.scale(1 / s, 1 / s)
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        cr.restore()

        target = self.drag_rect
        if target is None and self.hover is not None:
            h = self.hover
            target = (h["x"], h["y"], h["w"], h["h"])

        # dim everything except the target
        cr.set_source_rgba(0, 0, 0, 0.38)
        cr.rectangle(0, 0, width, height)
        if target:
            x, y, w, h = target
            cr.rectangle(x + w, y, -w, h)  # opposite winding = hole
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.fill()

        if target:
            x, y, w, h = target
            cr.set_source_rgba(0.54, 0.71, 0.98, 1)  # Catppuccin blue
            cr.set_line_width(2)
            cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
            cr.stroke()
            if self.drag_rect:
                px = f"{round(w * s)} × {round(h * s)}"
                self._label(cr, px, x + 6, y + h + 20 if y + h + 28 < height else y - 10)

        if self.long_mode:
            hint = "长图:拖框选内容区域 / 点击窗口,开始自动滚动   ·   L 返回普通截图   ·   Esc 取消"
        else:
            hint = "拖框 = 选区   ·   点击窗口 = 整个窗口   ·   L 长图   ·   Esc 取消"
        self._label(cr, hint, width / 2, 34, center=True)

    def _label(self, cr, text, x, y, center=False):
        # Pango (not cairo's toy text API) so CJK text gets a proper fallback font.
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 11"))
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        if center:
            x -= tw / 2
        pad = 8
        top = y - th
        cr.set_source_rgba(0.07, 0.07, 0.11, 0.85)
        cr.rectangle(x - pad, top - pad, tw + 2 * pad, th + 2 * pad)
        cr.fill()
        cr.set_source_rgba(0.80, 0.84, 0.96, 1)
        cr.move_to(x, top)
        PangoCairo.show_layout(cr, layout)

    # ── input ────────────────────────────────────────────────
    def _on_motion(self, _ctl, x, y):
        if self.drag_start is None:
            hit = self._window_at(x, y)
            if hit is not self.hover:
                self.hover = hit
                self.area.queue_draw()

    def _on_drag_begin(self, _g, x, y):
        self.drag_start = (x, y)
        self.drag_rect = None

    def _on_drag_update(self, _g, dx, dy):
        if self.drag_start is None:
            return
        if abs(dx) < CLICK_SLOP and abs(dy) < CLICK_SLOP:
            self.drag_rect = None
        else:
            x0, y0 = self.drag_start
            self.drag_rect = (min(x0, x0 + dx), min(y0, y0 + dy), abs(dx), abs(dy))
        self.area.queue_draw()

    def _on_drag_end(self, _g, dx, dy):
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        self.drag_start = None
        if abs(dx) < CLICK_SLOP and abs(dy) < CLICK_SLOP:
            hit = self._window_at(x0, y0)
            rect = (hit["x"], hit["y"], hit["w"], hit["h"]) if hit else (0, 0, self.area.get_width(), self.area.get_height())
            self._finish(rect, "window" if hit else "screen")
        else:
            self._finish(self.drag_rect, "region")

    def _on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            _log("cancelled")
            self.dismiss()
            return True
        if keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.long_mode = not self.long_mode
            self.area.queue_draw()
            return True
        return False

    # ── output ───────────────────────────────────────────────
    def _finish(self, rect, kind):
        if self.long_mode:
            x, y, w, h = (int(round(v)) for v in rect)
            self.dismiss()
            if w >= 40 and h >= 80:
                # start after the overlay is gone so it is not in the frames
                GLib.timeout_add(150, self.app.start_long, (x, y, w, h))
            return
        # Whatever happens, never leave the frozen overlay covering the screen.
        try:
            self._export(rect, kind)
        except Exception as e:  # noqa: BLE001
            _log(f"export failed: {e!r}")
        finally:
            self.dismiss()

    def _export(self, rect, kind):
        s = self._scale()
        x, y, w, h = rect
        # clamp to the screenshot, convert logical -> native pixels
        sx, sy = max(0, round(x * s)), max(0, round(y * s))
        sw = min(self.surface.get_width() - sx, round(w * s))
        sh = min(self.surface.get_height() - sy, round(h * s))
        if sw < 1 or sh < 1:
            return
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
        cr = cairo.Context(out)
        cr.set_source_surface(self.surface, -sx, -sy)
        cr.paint()
        buf = io.BytesIO()
        out.write_to_png(buf)
        png = buf.getvalue()
        path = "history"
        provider = Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(png))
        self.get_clipboard().set_content(provider)
        self.app.history.add_image(png)
        GLib.idle_add(self.app.picker.prerender)
        _log(f"captured kind={kind} size={sw}x{sh} file={path}")


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.capture = None
        self.overlay = None
        self.long = None  # running long capture state
        self.windows = []
        self._reg_id = None
        act = Gio.SimpleAction.new("capture", None)
        act.connect("activate", lambda *_: self.trigger())
        self.add_action(act)

    def do_dbus_register(self, connection, object_path):
        node = Gio.DBusNodeInfo.new_for_xml(DBUS_XML)
        self._reg_id = connection.register_object(OBJECT_PATH, node.interfaces[0], self._on_dbus_call)
        return Gtk.Application.do_dbus_register(self, connection, object_path)

    def do_dbus_unregister(self, connection, object_path):
        if self._reg_id:
            connection.unregister_object(self._reg_id)
            self._reg_id = None
        Gtk.Application.do_dbus_unregister(self, connection, object_path)

    def _on_dbus_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "UpdateWindows":
            try:
                self.windows = json.loads(params.unpack()[0])
            except (ValueError, TypeError):
                pass
            invocation.return_value(None)
        elif method == "Capture":
            invocation.return_value(None)
            GLib.idle_add(self.trigger)
        elif method == "ShowHistory":
            invocation.return_value(None)
            GLib.idle_add(self.show_history)
        elif method == "TestEditLatest":
            invocation.return_value(None)
            img = next((i for i in self.history.items if i["kind"] == "image"), None)
            if img:
                GLib.idle_add(lambda: self._edit_image(img) or False)
        elif method == "TestLong":
            x, y, w, h, keys = params.unpack()
            invocation.return_value(None)
            GLib.idle_add(lambda: self.start_long((x, y, w, h), down_keys=keys) or False)
        elif method == "TestEditorTool":
            invocation.return_value(None)
            tool = params.unpack()[0]
            GLib.idle_add(lambda: self.editor.set_tool(tool) or False)
        elif method == "TestEditorArrowAndFinish":
            invocation.return_value(None)

            def go():
                ed = self.editor
                if ed.base is not None and ed.get_visible():
                    w, h = ed.base.get_width(), ed.base.get_height()
                    ed._push_undo()
                    ed.objects.append({"kind": "arrow", "p1": (w * 0.1, h * 0.1), "p2": (w * 0.8, h * 0.7),
                                       "color": ed.color, "width": 6})
                    ed.finish()
                return False
            GLib.idle_add(go)
        elif method == "TestHideHistory":
            invocation.return_value(None)
            GLib.idle_add(lambda: self.picker.close_picker() or False)
        elif method in ("TestSelect", "TestClick"):
            args = params.unpack()
            invocation.return_value(None)
            GLib.idle_add(self._test_input, method, args)

    def _test_input(self, method, args):
        ov = self.overlay
        if not ov.get_visible() or ov.surface is None:
            _log(f"{method} ignored: overlay not visible")
            return False
        if method == "TestSelect":
            ov._finish(tuple(args), "region")
        else:
            x, y = args
            hit = ov._window_at(x, y)
            rect = (hit["x"], hit["y"], hit["w"], hit["h"]) if hit else (0, 0, ov.area.get_width(), ov.area.get_height())
            ov._finish(rect, "window" if hit else "screen")
        return False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()  # stay resident without a visible window
        self.capture = KWinCapture()
        self.overlay = Overlay(self)
        self.history = History(log=_log)
        if not self.history.items:
            self._seed_history()
        self._migrate_legacy_captures()
        self.history.prune()
        self.picker = Picker(self, self.history, on_paste=self._paste_into_focused, on_edit=self._edit_image)
        self.editor = Editor(self, on_done=self._edit_done)
        self._watch_klipper()
        GLib.idle_add(self.picker.prerender)
        GLib.idle_add(self._reload_window_feed)
        _log("ready")

    # ── long (scrolling) capture ─────────────────────────────
    LONG_SETTLE_MS = 260     # wait after each wheel burst for smooth scrolling to finish
    LONG_MAX_FRAMES = 200

    def start_long(self, rect, down_keys=0):
        x, y, w, h = rect
        notches = max(1, int(h * 0.45 / 60))  # ~60 px per wheel notch; keep overlap
        self.long = {"rect": rect, "st": Stitcher(), "notches": notches, "stop": False, "down_keys": down_keys,
                     # self-scrolling content (tests) may start late: be more patient
                     "stall_limit": 12 if down_keys < 0 else 2,
                     "frames": 0, "stalls": 0, "t0": time.time()}
        _log(f"long capture start rect={rect} notches={notches}")
        GLib.idle_add(self._long_step)
        return False

    def _long_step(self):
        L = self.long
        if L is None:
            return False
        try:
            surf, _ = self.capture.area(*L["rect"])
        except CaptureError as e:
            _log(f"long capture failed: {e}")
            return self._long_finish()
        L["frames"] += 1
        st = L["st"]
        if st.add(surf) or L["frames"] == 1:
            L["stalls"] = 0
            if L["stop"] or L["frames"] >= self.LONG_MAX_FRAMES or st.height >= st.max_height:
                return self._long_finish()
            return self._long_scroll()
        # nothing new: smooth scrolling may still be running, look once more before ending
        L["stalls"] += 1
        if L["stalls"] >= L["stall_limit"] or L["stop"] or st.height >= st.max_height:
            return self._long_finish()
        GLib.timeout_add(self.LONG_SETTLE_MS, self._long_step)
        return False

    def _long_scroll(self):
        L = self.long
        if L["stop"]:
            return self._long_finish()
        env = dict(os.environ, YDOTOOL_SOCKET=os.environ.get("YDOTOOL_SOCKET") or
                   os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), ".ydotool_socket"))
        if L["down_keys"] < 0:  # tests: the content scrolls by itself, send no input at all
            GLib.timeout_add(self.LONG_SETTLE_MS, self._long_step)
            return False
        if L["down_keys"]:
            cmd = ["ydotool", "key"] + ["108:1", "108:0"] * L["down_keys"]  # Down arrow (tests)
        else:
            cmd = ["ydotool", "mousemove", "-w", "-x", "0", "-y", str(-L["notches"])]
        try:
            subprocess.run(cmd, env=env, timeout=5, check=False, capture_output=True)
        except (OSError, subprocess.TimeoutExpired) as e:
            _log(f"wheel failed: {e}")
            return self._long_finish()
        GLib.timeout_add(self.LONG_SETTLE_MS, self._long_step)
        return False

    def _long_finish(self):
        L, self.long = self.long, None
        out = L["st"].render()
        if out is None:
            return False
        buf = io.BytesIO()
        out.write_to_png(buf)
        png = buf.getvalue()
        path = "history"
        self.overlay.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(png)))
        self.history.add_image(png)
        GLib.idle_add(self.picker.prerender)
        _log(f"long capture done frames={L['frames']} size={out.get_width()}x{out.get_height()} "
             f"in {time.time() - L['t0']:.1f}s file={path}")
        return False

    # ── clipboard history ────────────────────────────────────
    def _watch_klipper(self):
        try:
            self.capture._bus.add_signal_receiver(
                lambda: GLib.timeout_add(120, self._record_clipboard),
                signal_name="clipboardHistoryUpdated", dbus_interface="org.kde.klipper.klipper")
        except Exception as e:  # noqa: BLE001
            _log(f"klipper watch failed: {e}")

    def _seed_history(self):
        """First run: import Klipper's text history and earlier captures."""
        texts = []
        try:
            k = self.capture._bus.get_object("org.kde.klipper", "/klipper")
            texts = [str(t) for t in dbus_iface(k, "org.kde.klipper.klipper").getClipboardHistoryMenu()]
        except Exception as e:  # noqa: BLE001
            _log(f"klipper seed failed: {e}")
        self.history.seed(texts, [])
        _log(f"seeded history: {len(texts)} texts")

    def _migrate_legacy_captures(self):
        """v0.0.1 also wrote every capture to history_dir(); keep each image once.

        Files already in the history are deleted (same bytes, same hash); missing
        ones are imported with their original time, then deleted.
        """
        legacy = sorted(history_dir().glob("*.png"), key=lambda p: p.stat().st_mtime)
        if not legacy:
            return
        known = {i["hash"] for i in self.history.items}
        imported = 0
        for p in legacy:
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() not in known:
                self.history.add_image(data)
                self.history.items[0]["ts"] = p.stat().st_mtime
                imported += 1
            p.unlink()
        self.history.items.sort(key=lambda i: i["ts"], reverse=True)
        self.history._save()
        _log(f"migrated legacy captures: {len(legacy)} file(s), {imported} imported, duplicates removed")

    def _record_clipboard(self):
        # Our own captures/edits are recorded directly; skip when we own the selection.
        if Gdk.Display.get_default().get_clipboard().is_local():
            return False
        self.history.record_current_async(done=self.picker.prerender)
        return False

    def show_history(self):
        t0 = time.time() * 1000
        self.picker.open()
        _log(f"history shown items={len(self.history.items)} in {time.time() * 1000 - t0:.0f} ms")
        return False

    def _paste_into_focused(self, item):
        # After the picker hides, focus returns to the previous window; send the
        # platform paste chord through uinput (Meta+V; a key remapper such as
        # xremap translates it per application, terminals included).
        GLib.timeout_add(180, self._send_paste)

    def _send_paste(self):
        sock = os.environ.get("YDOTOOL_SOCKET") or os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), ".ydotool_socket")
        env = dict(os.environ, YDOTOOL_SOCKET=sock)
        paste = os.environ.get("SNAPSHOT_KWIN_PASTE_KEYS", "125:1 47:1 47:0 125:0").split()
        try:
            subprocess.run(["ydotool", "key", *paste], env=env, timeout=2, check=False, capture_output=True)
        except (OSError, subprocess.TimeoutExpired) as e:
            _log(f"paste key failed: {e}")
        return False

    def _edit_image(self, item):
        self.editor.open_png(self.history.path(item))
        _log(f"editing {item['id']}")

    def _edit_done(self, surface, window):
        """Enter in the editor: the result becomes clipboard item #1 (and history)."""
        buf = io.BytesIO()
        surface.write_to_png(buf)
        png = buf.getvalue()
        window.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(png)))
        self.history.add_image(png)
        GLib.idle_add(self.picker.prerender)
        _log(f"edit saved to clipboard size={surface.get_width()}x{surface.get_height()}")

    def _reload_window_feed(self):
        # The KWin script pushes the window list only on changes; reloading it makes
        # it push once now, so a freshly (re)started daemon is not blind.
        try:
            s = self.capture._bus.get_object("org.kde.KWin", "/Scripting")
            scripting = dbus_iface(s, "org.kde.kwin.Scripting")
            scripting.unloadScript("snapshot-kwin-windows")
            scripting.start()
        except Exception as e:  # noqa: BLE001 - best effort, capture still works
            _log(f"window feed reload failed: {e}")
        return False

    def do_activate(self):
        pass  # activation alone does nothing; the "capture" action / Capture() does the work

    def trigger(self):
        if self.long is not None:
            self.long["stop"] = True
            return False
        t0 = time.time() * 1000
        if self.overlay.get_visible():
            return False
        try:
            surface, _scale = self.capture.workspace()
        except CaptureError as e:
            _log(f"capture failed: {e}")
            return False
        _log(f"grabbed in {time.time() * 1000 - t0:.0f} ms windows={len(self.windows)}")
        self.overlay.show_capture(surface, list(self.windows), t0)
        return False


def main():
    # dbus-python needs the GLib main loop to deliver signals (Klipper changes)
    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
