# Changelog

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
