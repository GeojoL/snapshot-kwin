# snapshot-kwin

Instant screenshots for **KDE Plasma 6 on Wayland**.

Press the hotkey and the screen freezes in ~150 ms (Spectacle takes 0.7–1.4 s on
the same machine). Drag to capture a region, or click a window to capture all of
it. The image goes straight to the clipboard. There is no editor popup: you
annotate later, from the clipboard history.

> Status: early. Region and window capture work. Long (scrolling) capture,
> the clipboard history picker and the annotation editor are in progress.

## Why it is fast

| Step (2560×1440, RTX 5060, KWin 6.7.5) | Time |
|---|---|
| KWin `org.kde.KWin.ScreenShot2.CaptureWorkspace`, all pixels read | 55–75 ms |
| snapshot-kwin: hotkey → overlay activated by KWin | ~150 ms |
| Spectacle (resident) | 700–800 ms |
| Spectacle (cold start) | ~1400 ms |

Most of Spectacle's latency is in its own UI startup, not in KWin. snapshot-kwin
keeps a small GTK4 daemon running with the overlay already built, so a capture
costs one KWin grab and one frame.

## How it works

- **Capture**: KWin's restricted `ScreenShot2` D-Bus API. KWin authorizes callers
  by matching the caller's executable path against a `.desktop` file that declares
  `X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2`. The installer copies
  the Python interpreter to a private path and authorizes **only that copy**, so
  your system Python does not gain screen-capture rights.
- **Click-to-capture**: a tiny KWin script pushes visible window geometry to the
  daemon whenever windows change, so a click resolves the window instantly.
- **UI**: Python + GTK4 + cairo, all from the distro; nothing to pip-install.

## Install (per user, no root)

Requirements: Plasma 6 Wayland, `python3` with `gi` (GTK 4), `pycairo` and
`dbus-python`, plus `kpackagetool6` and `kwriteconfig6`.

```sh
./install.sh             # install / update
./install.sh --uninstall
```

Then bind a global shortcut to `~/.local/bin/snapshot-kwin-capture`
(System Settings → Shortcuts → Add Command).

Captures are saved to `${XDG_STATE_HOME:-~/.local/state}/snapshot-kwin/history/`.

## Roadmap

- [x] Instant region / window capture to clipboard
- [ ] Long capture: auto-scroll the window under the pointer and stitch
- [ ] Clipboard history (text + images) as the entry point for editing
- [ ] Editor: pen, highlighter, line, arrow, rectangle, ellipse, text, numbered
      stamps, pixelate/blur, eraser; crop, rotate, flip, resize; undo/redo
- [ ] Multi-monitor and fractional scaling

Design notes (Chinese): [docs/design-notes.zh.md](docs/design-notes.zh.md)

## License

MIT
