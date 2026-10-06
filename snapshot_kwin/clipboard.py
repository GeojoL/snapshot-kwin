"""Clipboard history (text + images) and the picker window.

KWin does not expose a data-control protocol, so an ordinary client cannot watch
the clipboard in the background. Klipper (Plasma's clipboard service) can, and
emits org.kde.klipper.klipper.clipboardHistoryUpdated on every change; on that
signal we read the current selection with wl-paste and record it with a
timestamp, so text and images share one ordered history.

Picker: Enter pastes into the previously focused window; double-click an image
to edit it (double-click text pastes it); Shift+Enter also edits; Esc closes;
typing filters text entries. Picking, editing, dragging out or clicking another
window closes (destroys) the picker; opening it while open brings it to the front.
"""
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

# Retention (overridable in ${XDG_CONFIG_HOME:-~/.config}/snapshot-kwin/config.json):
#   {"max_age_days": 183, "max_bytes": 5368709120}
DEFAULT_MAX_AGE_DAYS = 183           # about half a year
DEFAULT_MAX_BYTES = 5 * 1024 ** 3    # 5 GiB of stored images
PICKER_ROWS = 300                    # newest rows rendered in the picker (search covers all)


def load_config():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    cfg = {"max_age_days": DEFAULT_MAX_AGE_DAYS, "max_bytes": DEFAULT_MAX_BYTES}
    try:
        cfg.update(json.loads((Path(base) / "snapshot-kwin" / "config.json").read_text()))
    except (OSError, ValueError):
        pass
    return cfg
PICKER_TITLE = "snapshot-kwin-clipboard"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    d = Path(base) / "snapshot-kwin" / "clipboard"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(args, timeout=2.0):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


class History:
    """Ordered, de-duplicated clipboard history persisted as index.json + PNG files."""

    def __init__(self, log=print):
        self.dir = state_dir()
        self.index = self.dir / "index.json"
        self.items = []  # newest first: {"id","ts","kind","text"|"file","hash"}
        self.version = 0  # bumped on every change; the picker re-renders only when it moved
        self.config = load_config()
        self.log = log
        self._load()

    def _load(self):
        try:
            data = json.loads(self.index.read_text())
            self.items = [i for i in data if i.get("kind") == "text" or (self.dir / i.get("file", "")).exists()]
        except (OSError, ValueError):
            self.items = []

    def prune(self, save=True):
        """Drop items older than max_age_days, then oldest images until under max_bytes."""
        cutoff = time.time() - float(self.config["max_age_days"]) * 86400
        keep, drop = [], []
        for i in self.items:
            (keep if i["ts"] >= cutoff else drop).append(i)
        total = sum(self._size(i) for i in keep)
        limit = int(self.config["max_bytes"])
        for i in reversed(list(keep)):  # oldest first
            if total <= limit:
                break
            if i["kind"] == "image":
                total -= self._size(i)
                keep.remove(i)
                drop.append(i)
        for i in drop:
            if i["kind"] == "image":
                (self.dir / i["file"]).unlink(missing_ok=True)
        if drop:
            self.items = keep
            self.log(f"pruned {len(drop)} item(s); stored images {total / 1e6:.1f} MB")
            if save:
                self._save()

    def _size(self, item):
        if item["kind"] != "image":
            return len(item.get("text", "").encode())
        try:
            return (self.dir / item["file"]).stat().st_size
        except OSError:
            return 0

    def total_bytes(self):
        return sum(self._size(i) for i in self.items)

    def _save(self):
        self.version += 1
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False))
        os.replace(tmp, self.index)

    def _add(self, kind, payload: bytes, text=None):
        h = hashlib.sha256(payload).hexdigest()
        existing = next((i for i in self.items if i["hash"] == h), None)
        if existing is not None:
            # already known: just move it to the top
            self.items.remove(existing)
            existing["ts"] = time.time()
            self.items.insert(0, existing)
        else:
            item = {"id": h[:16], "ts": time.time(), "kind": kind, "hash": h}
            if kind == "image":
                name = f"{h[:16]}.png"
                (self.dir / name).write_bytes(payload)
                item["file"] = name
            else:
                item["text"] = text
            self.items.insert(0, item)
        self.prune(save=False)
        self._save()

    def seed(self, texts_newest_first, pngs_oldest_first):
        now = time.time()
        for i, t in enumerate(reversed(texts_newest_first)):
            if t.strip():
                self._add("text", t.encode(), text=t)
                self.items[0]["ts"] = now - 86400 + i
        for p in pngs_oldest_first:
            self._add("image", p.read_bytes())
            self.items[0]["ts"] = p.stat().st_mtime
        self.items.sort(key=lambda i: i["ts"], reverse=True)
        self._save()

    def add_image(self, png: bytes):
        """Record an image we produced ourselves (a capture or an edit)."""
        self._add("image", png)

    def record_current_async(self, done=None):
        """Read the current selection off the main thread, then record it on it.

        wl-paste asks the selection owner for the data. If the owner is this very
        process and we waited synchronously, nobody would answer (deadlock until
        timeout), so the read happens in a worker thread.
        """
        def work():
            got = self._read_selection()
            GLib.idle_add(finish, got)

        def finish(got):
            if got is not None:
                kind, payload, text = got
                self._add(kind, payload, text=text)
            if done:
                done()
            return False

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _read_selection():
        types = _run(["wl-paste", "--list-types"])
        if types is None or types.returncode != 0:
            return None
        mimes = types.stdout.decode(errors="replace").split()
        if "image/png" in mimes:
            r = _run(["wl-paste", "--no-newline", "--type", "image/png"], timeout=5)
            if r and r.returncode == 0 and r.stdout:
                return ("image", r.stdout, None)
        if any(m.startswith("text/") or m in ("UTF8_STRING", "STRING", "TEXT") for m in mimes):
            r = _run(["wl-paste", "--no-newline"])
            if r and r.returncode == 0 and r.stdout.strip():
                text = r.stdout.decode(errors="replace")
                return ("text", text.encode(), text)
        return None

    def path(self, item) -> Path:
        return self.dir / item["file"]


class Picker:
    """The history list is built once and kept rendered; the window around it is
    created on open and destroyed on close (Esc, pick, edit, drag-out, or focus
    moving elsewhere). A re-shown hidden window is not reliably activated by KWin,
    a new one is, so every open gets a fresh toplevel."""

    def __init__(self, app, history, on_paste, on_edit, on_raise, log=lambda _m: None):
        self.app = app
        self.log = log
        self.history = history
        self.on_paste = on_paste
        self.on_edit = on_edit
        self.on_raise = on_raise  # on_raise(title): ask KWin to activate that window
        self.win = None
        self._dragging = False
        self._rendered_version = -1

        css = Gtk.CssProvider()
        css.load_from_string("""
            .snapshot-kwin-picker { background: #1e1e2e; border: 1px solid #45475a; border-radius: 12px; }
            .snapshot-kwin-picker entry { margin: 12px; }
            .snapshot-kwin-picker row { padding: 8px 12px; color: #cdd6f4; }
            .snapshot-kwin-picker row:selected { background: #313244; border-radius: 8px; }
            .snapshot-kwin-picker .hint { color: #7f849c; margin: 6px 12px 10px; font-size: 0.9em; }
        """)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search = Gtk.SearchEntry(placeholder_text="搜索文字…")
        self.search.connect("search-changed", lambda *_: self._on_search())
        self.box.append(self.search)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=False)
        self.listbox.set_filter_func(self._filter)
        # row-activated fires on double-click (activate_on_single_click=False):
        # images open the editor, text is pasted. Enter is handled in _on_key.
        self.listbox.connect("row-activated", lambda _lb, row: self._activate(row, edit=row.item["kind"] == "image"))
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.listbox)
        self.box.append(scroller)

    def get_visible(self):
        return self.win is not None

    # ── content ──────────────────────────────────────────────
    def _on_search(self):
        q = self.search.get_text().strip().lower()
        if q:
            # search the whole history, not only the rendered newest rows
            hits = [i for i in self.history.items if i["kind"] == "text" and q in i["text"].lower()]
            self.refresh(hits[:PICKER_ROWS])
            self._rendered_version = -1  # next plain open re-renders the default list
        else:
            self.refresh()
            self._rendered_version = self.history.version

    def refresh(self, items=None):
        child = self.listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt
        for item in (items if items is not None else self.history.items[:PICKER_ROWS]):
            row = Gtk.ListBoxRow()
            row.item = item
            if item["kind"] == "image":
                pic = Gtk.Picture.new_for_filename(str(self.history.path(item)))
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                pic.set_size_request(-1, 96)
                pic.set_halign(Gtk.Align.START)
                row.set_child(pic)
            else:
                text = " ".join(item["text"].split())
                lbl = Gtk.Label(label=text[:400], xalign=0, wrap=True, lines=2,
                                ellipsize=Pango.EllipsizeMode.END)
                row.set_child(lbl)
            drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
            drag.connect("prepare", self._drag_prepare, item)
            drag.connect("drag-begin", self._drag_begin)
            drag.connect("drag-end", self._drag_end)
            row.add_controller(drag)
            self.listbox.append(row)
        first = self.listbox.get_row_at_index(0)
        if first:
            self.listbox.select_row(first)

    # ── drag out ─────────────────────────────────────────────
    def _drag_prepare(self, _src, _x, _y, item):
        """Rows drag out like files: images as the PNG file (path drop in a
        terminal, file drop in a browser/chat) plus raw image/png; text as text."""
        if item["kind"] == "image":
            path = self.history.path(item)
            # Plain text/uri-list, not a GdkFileList value: GTK would offer the file
            # through the document portal (application/vnd.portal.filetransfer) first,
            # and Ghostty fails to convert that ("Could not convert data ... to
            # GdkFileList"), so the drop did nothing. text/plain gives terminals the path.
            uri = Gio.File.new_for_path(str(path)).get_uri()
            return Gdk.ContentProvider.new_union([
                Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(f"{uri}\r\n".encode())),
                Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(str(path).encode())),
                Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(path.read_bytes())),
            ])
        return Gdk.ContentProvider.new_for_value(item["text"])

    def _drag_begin(self, src, _drag):
        self._dragging = True  # the drop target may take focus; close on drag-end instead
        row = src.get_widget()
        src.set_icon(Gtk.WidgetPaintable.new(row.get_child()), 0, 0)

    def _drag_end(self, *_):
        self._dragging = False
        self.close_picker("drag")

    def prerender(self):
        """Render in the background (while hidden) so opening is instant."""
        if not self.get_visible() and self._rendered_version != self.history.version:
            self.refresh()
            self._rendered_version = self.history.version
        return False

    def _filter(self, _row):
        return True  # filtering is done by _on_search over the whole history

    # ── open / close ─────────────────────────────────────────
    def open(self):
        """Open the picker, or bring it to the front if it is already open."""
        if self.win is not None:
            self.on_raise(PICKER_TITLE)
            return "raised"
        self.search.set_text("")
        if self._rendered_version != self.history.version:
            self.refresh()
            self._rendered_version = self.history.version
        else:
            first = self.listbox.get_row_at_index(0)
            if first:
                self.listbox.select_row(first)
        win = Gtk.Window(application=self.app, title=PICKER_TITLE, decorated=False,
                         default_width=760, default_height=560, resizable=False)
        win.add_css_class("snapshot-kwin-picker")
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        win.add_controller(keys)
        win.connect("notify::is-active", self._on_active_changed)
        win.connect("close-request", lambda *_: self.close_picker("close-request") or True)
        win.set_child(self.box)
        self.win = win
        win.present()
        self.listbox.grab_focus()
        first = self.listbox.get_selected_row()
        if first:
            first.grab_focus()
        # without an activation token KWin may map the window unfocused; activate it
        GLib.timeout_add(200, self._ensure_active, win)
        return "opened"

    def _ensure_active(self, win):
        if self.win is win and not win.is_active():
            self.on_raise(PICKER_TITLE)
        return False

    def _on_active_changed(self, win, _pspec):
        # clicking another window (or anything that takes focus) closes the picker
        if self.win is win and not win.is_active() and not self._dragging:
            GLib.idle_add(lambda: self.win is win and self.close_picker("focus lost") or False)

    def close_picker(self, reason="closed"):
        win, self.win = self.win, None
        if win is None:
            return
        win.set_child(None)  # keep the rendered list for the next open
        win.destroy()
        self.log(f"history closed ({reason})")

    # ── keys ─────────────────────────────────────────────────
    def _on_key(self, _ctl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            self.close_picker("esc")
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self.listbox.get_selected_row()
            if row is not None:
                self._activate(row, edit=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._move(1 if keyval == Gdk.KEY_Down else -1)
            return True
        return False

    def _move(self, step):
        row = self.listbox.get_selected_row()
        i = row.get_index() if row else -1
        while True:
            i += step
            nxt = self.listbox.get_row_at_index(i)
            if nxt is None:
                return
            if nxt.get_child_visible() and self._filter(nxt):
                self.listbox.select_row(nxt)
                nxt.grab_focus()
                return

    def _activate(self, row, edit):
        item = row.item
        if edit:
            if item["kind"] == "image":
                self.close_picker("edit")
                self.on_edit(item)
            return
        # Set the clipboard while we still have focus (Wayland needs a recent input serial).
        clip = self.win.get_clipboard()
        if item["kind"] == "image":
            data = self.history.path(item).read_bytes()
            clip.set_content(Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(data)))
        else:
            clip.set(item["text"])
        self.close_picker("paste")
        self.on_paste(item)
