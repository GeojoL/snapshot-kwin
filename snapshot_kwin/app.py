"""snapshot-kwin daemon: instant region / window capture on KDE Plasma (Wayland).

Workflow (see docs/design-notes.zh.md):
  hotkey -> frozen fullscreen overlay -> drag = region, click = whole window
  -> PNG goes to the clipboard and to the history dir. No editor pops up.

The process stays resident and keeps the overlay window built, so a capture only
costs the KWin grab (~60 ms) plus one frame.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

import cairo  # noqa: E402

import dbus  # noqa: E402

from .capture import CaptureError, KWinCapture  # noqa: E402
from .clipboard import History, Picker  # noqa: E402


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

        self._label(cr, "拖框 = 选区   ·   点击窗口 = 整个窗口   ·   Esc 取消", width / 2, 34, center=True)

    def _label(self, cr, text, x, y, center=False):
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(14)
        ext = cr.text_extents(text)
        if center:
            x -= ext.width / 2
        pad = 8
        cr.set_source_rgba(0.07, 0.07, 0.11, 0.85)
        cr.rectangle(x - pad, y - ext.height - pad, ext.width + 2 * pad, ext.height + 2 * pad)
        cr.fill()
        cr.set_source_rgba(0.80, 0.84, 0.96, 1)
        cr.move_to(x, y)
        cr.show_text(text)

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
        return False

    # ── output ───────────────────────────────────────────────
    def _finish(self, rect, kind):
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
        path = history_dir() / (time.strftime("%Y%m%d-%H%M%S-") + f"{int(time.time() * 1000) % 1000:03d}.png")
        out.write_to_png(str(path))
        png = path.read_bytes()
        provider = Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(png))
        self.get_clipboard().set_content(provider)
        _log(f"captured kind={kind} size={sw}x{sh} file={path}")


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.capture = None
        self.overlay = None
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
        self.picker = Picker(self, self.history, on_paste=self._paste_into_focused, on_edit=self._edit_image)
        self._watch_klipper()
        GLib.idle_add(self.picker.prerender)
        GLib.idle_add(self._reload_window_feed)
        _log("ready")

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
        pngs = sorted(history_dir().glob("*.png"), key=lambda p: p.stat().st_mtime)
        self.history.seed(texts, pngs)
        _log(f"seeded history: {len(texts)} texts, {len(pngs)} images")

    def _record_clipboard(self):
        self.history.record_current()
        GLib.idle_add(self.picker.prerender)
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
        _log(f"edit requested for {item['id']} (editor not implemented yet)")

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
