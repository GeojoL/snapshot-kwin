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
- **No animation**: a tiny KWin effect grabs only snapshot-kwin's own windows on
  open/close, so Plasma's Scale animation skips them (every other window keeps
  its animation; no global setting is changed).

## Install (per user, no root)

Requirements: Plasma 6 Wayland, `python3` with `gi` (GTK 4), `pycairo` and
`dbus-python`, plus `kpackagetool6` and `kwriteconfig6`.

```sh
./install.sh             # install / update
./install.sh --uninstall
```

Shortcuts are set up by the installer (see *Find it, turn it on/off*).

Captures and the clipboard history live in
`${XDG_STATE_HOME:-~/.local/state}/snapshot-kwin/clipboard/` (each image once).
Retention defaults to half a year and 5 GiB of images; override in
`${XDG_CONFIG_HOME:-~/.config}/snapshot-kwin/config.json`:

```json
{"max_age_days": 183, "max_bytes": 5368709120}
```

## Find it, turn it on/off

- App menu / KRunner: search **snapshot** or **截图** (`snapshot-kwin.desktop`).
  Right-click it for 剪贴板历史 / 开启 / 关闭.
- Shortcuts: **Meta+Alt+1, Print, Meta+Shift+Print** capture (Meta+Shift+S stays Save As);
  **Meta+Shift+V** clipboard history. `install.sh` runs `tools/take-over-shortcuts`,
  which clears every Spectacle *screenshot* key (Spectacle keeps only screen
  recording: Meta+Shift+R etc.), so snapshot-kwin is the only screenshot tool.
- With a key remapper (xremap) that ignores extra modifiers, pass the capture keys
  through first, or app mappings swallow them (Chrome's Meta+1 → Ctrl+1 ate
  Meta+Alt+1): see profilo `linux/xremap/config.yml`, group `kwin passthrough`.
- Scripts and agents: `snapshot-kwin-shot [OUT.png]` saves the whole desktop silently
  (no overlay, clipboard untouched) and prints the path. Do not use `spectacle -b`.
- It is a user service: `systemctl --user status|start|stop snapshot-kwin`;
  `disable --now` turns it off for good, `enable --now` brings it back.
- Logs: `journalctl --user -u snapshot-kwin`. Source: this repo; `./install.sh` rewires everything.

## Clipboard history: paste and drag

Open the history (bind a shortcut to `~/.local/bin/snapshot-kwin-history`).
Enter makes the selected row the current clipboard content and the newest history
entry, then pastes it into the previous window; a later Meta+V pastes it again.
Shift+Enter or double-click edits an image.
Drag the 剪贴板历史 strip at the top to move the window and any edge or corner to
resize it; KWin reports the new geometry and the next open restores it
(`~/.local/state/snapshot-kwin/picker.json`).
Rows can be **dragged out**: an image drops as its PNG file (a path in a terminal,
an attachment in a browser or chat) and also offers raw `image/png`; text drops as text.

### Pasting images in a terminal (`tools/term-paste`)

Terminals paste only text, so `install.sh` links `~/.local/bin/term-paste`; bind
your terminal's paste key to it (for example an xremap mapping of Meta+V in Ghostty,
see profilo `linux/xremap/config.yml`). It works through tmux and sends no synthetic keys:

| Clipboard | Active tmux pane | Result |
|---|---|---|
| text | any | bracketed paste of the text |
| image | Claude Code | Ctrl+V, Claude reads the image from the clipboard |
| image | anything else | PNG saved to `~/.local/state/snapshot-kwin/paste/`, path pasted |

Needs `wl-paste` and a tmux client in the focused terminal.

## Tests

```sh
python3 -m unittest discover -s tests   # rendering, hit-testing, crop math, stitching
tests/e2e/run.sh                        # end-to-end in an invisible nested KWin
```

The end-to-end suite starts `kwin_wayland --virtual` on a private D-Bus session
with throwaway config/state directories and drives the daemon through test-only
D-Bus hooks, so it never touches your desktop, focus, input or clipboard. The
long-capture scenario uses a terminal whose output scrolls by itself, so no input
is injected at all. It also runs in its own mount namespace, so a portal started
on the private bus cannot unmount the desktop's Flatpak document portal. Run both
suites before committing a change.

## Roadmap

- [x] Instant region / window capture to clipboard
- [x] Long capture: auto-scroll the window under the pointer and stitch (press L in the overlay)
- [x] Clipboard history (text + images) as the entry point for editing
- [x] Editor: pen, highlighter, line, arrow, rectangle, ellipse, text, numbered
      stamps, pixelate/blur, eraser; crop, rotate, flip, resize; undo/redo
- [ ] Multi-monitor and fractional scaling

Design notes (Chinese): [docs/design-notes.zh.md](docs/design-notes.zh.md)

## License

MIT
