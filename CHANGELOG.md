# Changelog

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
  desktop) and a nightly systemd timer; 23 unit tests.

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
