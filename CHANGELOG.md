# Changelog

## v0.0.7 — 2026-10-07

- Protected mode reduced to what it is for: a system unit that only root can
  stop or disable, restarted when killed. Removed again: watchdog/sd_notify,
  interpreter re-sync, root-owned code copy, restart back-off. The per-user unit
  is back to the v0.0.5 one. The protected unit runs the checkout's code; only
  the authorized interpreter copy lives in /usr/local/libexec (SELinux does not
  let a system service execute files from a home directory).

## v0.0.6 — 2026-10-07

- Service hardening: `Type=notify` with a 30 s watchdog (a hung main loop is
  restarted, not just a crash), `Restart=always` with no start limit (1 s,
  backing off to 10 s). Minimal built-in sd_notify, no new dependency.
- Optional protected mode, `sudo tools/install-system`: a root-owned system unit
  runs the daemon as the desktop user, so stopping or disabling it needs root;
  code and the authorized interpreter are root-owned copies in /usr/local; the
  interpreter copy is re-synced with python3 before every start; the unit waits
  for the Plasma Wayland session (`python -m snapshot_kwin.launch`).

## v0.0.5 — 2026-10-07

- Clipboard history recorded nothing copied elsewhere once the Plasma clipboard
  applet (and with it Klipper) was disabled. The daemon now watches the clipboard
  itself with `wl-paste --watch` (KWin's ext-data-control; no focus needed) and
  restarts the watcher if it exits; Klipper's signal remains a fallback.
- No more skipping on `Gdk.Clipboard.is_local()`: on Wayland an unfocused client
  is not told it lost the selection, so after a capture it could stay True.
- A long text without spaces (URL, path) no longer sets the clipboard history
  window's minimum width (it forced ~1360 px, so the window could not be narrowed).

## v0.0.4 — 2026-10-07

First public release.

- Clipboard history window can be moved (drag the title strip) and resized (drag
  an edge or corner); its geometry is remembered across opens (KWin reports it,
  KWin restores it, since a Wayland client cannot place itself).
- Clipboard history styling no longer depends on the GTK theme (the title strip
  was drawn as a light headerbar and the list white under the default theme).
- README rewritten in Chinese (English summary at the end) with real screenshots
  taken in a nested headless KWin.
- Enter in the clipboard history moves the confirmed entry to the top of the
  history as well as onto the clipboard, so Meta+V keeps pasting it.

- Replaces Spectacle for screenshots: `tools/take-over-shortcuts` gives Print,
  Meta+Shift+Print and Meta+Alt+1 to snapshot-kwin (Meta+Shift+S stays Save As) and clears
  Spectacle's screenshot keys (recording untouched). Run by `install.sh`.
- `snapshot-kwin-shot`: silent whole-desktop PNG via the new `ShotToFile` D-Bus method.
- Visible launcher (search 截图 / snapshot) with history / on / off actions;
  `install.sh` now creates every entry it relies on.
- Clipboard history rows can be dragged out (images as `text/uri-list` + path + `image/png`, text as text). No GdkFileList: its portal transfer fails in Ghostty.
- Clipboard history window closes (is destroyed) after paste, edit, drag-out, Esc, or
  a click elsewhere, and is created fresh on every open: a hidden window re-shown
  by Meta+Shift+V was often not activated. Meta+Shift+V while it is open brings
  it to the front (KWin activation).
- `tools/term-paste`: Meta+V in a terminal pastes text or images into the active
  tmux pane (Ctrl+V for Claude Code, a saved PNG path elsewhere); linked by `install.sh`.

## v0.0.3 — 2026-10-06

- Long capture scrolls the pane under the *center* of the selection: the pointer
  is parked there (closed-loop positioning that adapts to pointer acceleration)
  and restored afterwards. Fixes split layouts where the drag ended over a
  different pane. Cursor reads time out after 1 s, so a capture never hangs.

## v0.0.2 — 2026-10-06

- Long (scrolling) capture: press L in the overlay, drag the content area or
  click a window; auto-scrolls and stitches (robust to translucent windows,
  sticky headers/footers kept once). Second hotkey press stops it.
- Editor: drag any annotation to move it; select/move tool (V); Delete removes
  the selection; color/width restyle it. Crop box with 8 handles, move, thirds
  guides and ratios (free, 1:1, 4:3, 3:4, 16:9, 9:16, 3:2). Icon-only toolbar
  with 1 s hover tips; no status line.
- History: retention by age (183 days) and size (5 GiB of images), configurable
  in ~/.config/snapshot-kwin/config.json; captures stored once (v0.0.1
  duplicates migrated); picker renders the newest 300 rows, search covers all.
- No open/close animation for snapshot-kwin's windows; CJK overlay hints via
  Pango; captures were missing from history (self-owned selection) — fixed.
- Tests: isolated end-to-end suite in a nested invisible KWin (never touches the
  desktop; own mount namespace, so the desktop's Flatpak document portal stays
  mounted), run on demand before committing; 23 unit tests.

## v0.0.1 — 2026-10-06

First private release.

- Instant capture on KDE Plasma 6 Wayland: hotkey → frozen screen in ~150 ms
  (KWin ScreenShot2 grab ~60 ms). Drag = region, click = whole window. The image
  goes straight to the clipboard; no editor popup.
- Clipboard history (text + images), ordered and de-duplicated, recorded via
  Klipper's change signal; instant picker (Enter pastes into the focused window).
- Editor opened by double-clicking an image in the history: pen, highlighter,
  line, arrow, rectangle, ellipse, text (CJK), numbered stamps, mosaic, eraser,
  select/move; crop box with handles and ratios; rotate/flip; undo/redo; Enter
  puts the result on the clipboard as item #1.
- No open/close animation for snapshot-kwin's own windows (scoped KWin effect).
- Per-user install script; only a private interpreter copy is authorized for the
  restricted KWin screenshot API.
