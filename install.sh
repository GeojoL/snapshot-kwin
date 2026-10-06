#!/usr/bin/env bash
# snapshot-kwin user install (no root). Re-runnable.
#
#   ./install.sh            install / update for the current user
#   ./install.sh --uninstall
#
# Why a private interpreter copy: KWin only lets executables whose .desktop file
# declares X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2 call the
# screenshot API, matched by the caller's real executable path. Authorizing the
# system python would grant every python script screen capture, so we authorize
# only a dedicated copy that runs this daemon.
set -euo pipefail

APP_ID=io.github.geojol.SnapshotKwin
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
LIBEXEC="$HOME/.local/libexec/snapshot-kwin"
BIN="$HOME/.local/bin"
PY_COPY="$LIBEXEC/snapshot-kwin-python"
DESKTOP="$DATA_HOME/applications/$APP_ID.desktop"
UNIT="$CONFIG_HOME/systemd/user/snapshot-kwin.service"
TRIGGER="$BIN/snapshot-kwin-capture"
HISTORY="$BIN/snapshot-kwin-history"
TERM_PASTE="$BIN/term-paste"
SHOT="$BIN/snapshot-kwin-shot"
LAUNCHER="$DATA_HOME/applications/snapshot-kwin.desktop"
KWIN_SCRIPT_ID=snapshot-kwin-windows
KWIN_EFFECT_ID=snapshot-kwin-noanim

uninstall() {
  systemctl --user disable --now snapshot-kwin.service 2>/dev/null || true
  kpackagetool6 --type KWin/Script -r "$KWIN_SCRIPT_ID" >/dev/null 2>&1 || true
  kwriteconfig6 --file kwinrc --group Plugins --key "${KWIN_SCRIPT_ID}Enabled" --delete 2>/dev/null || true
  busctl --user call org.kde.KWin /Effects org.kde.kwin.Effects unloadEffect s "$KWIN_EFFECT_ID" >/dev/null 2>&1 || true
  kpackagetool6 --type KWin/Effect -r "$KWIN_EFFECT_ID" >/dev/null 2>&1 || true
  kwriteconfig6 --file kwinrc --group Plugins --key "${KWIN_EFFECT_ID}Enabled" --delete 2>/dev/null || true
  rm -f -- "$UNIT" "$DESKTOP" "$TRIGGER" "$HISTORY" "$TERM_PASTE" "$LAUNCHER" "$SHOT" "$DATA_HOME/applications/snapshot-kwin-capture.desktop" "$DATA_HOME/applications/snapshot-kwin-history.desktop"
  rm -rf -- "$LIBEXEC"
  systemctl --user daemon-reload
  echo "snapshot-kwin removed (history in \${XDG_STATE_HOME:-~/.local/state}/snapshot-kwin kept)"
}
[ "${1:-}" = "--uninstall" ] && { uninstall; exit 0; }

for cmd in python3 kpackagetool6 kwriteconfig6 busctl systemctl; do
  command -v "$cmd" >/dev/null || { echo "missing: $cmd" >&2; exit 1; }
done
python3 - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: F401
import cairo, dbus  # noqa: F401
PY

# 1. dedicated interpreter copy (real path is what KWin checks)
mkdir -p "$LIBEXEC" "$BIN" "$(dirname "$DESKTOP")" "$(dirname "$UNIT")"
install -m 0755 "$(readlink -f "$(command -v python3)")" "$PY_COPY.new"
mv -f "$PY_COPY.new" "$PY_COPY"
PY_REAL="$(readlink -f "$PY_COPY")"

# 2. authorization .desktop (hidden from menus)
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=snapshot-kwin
Comment=Instant screenshot daemon for KDE Plasma (Wayland)
Exec=$PY_REAL -m snapshot_kwin.app
NoDisplay=true
X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2
EOF
kbuildsycoca6 >/dev/null 2>&1 || true

# 3. KWin window-geometry feed (click-to-capture)
if kpackagetool6 --type KWin/Script -s "$KWIN_SCRIPT_ID" >/dev/null 2>&1; then
  kpackagetool6 --type KWin/Script -u "$SRC/kwin-script" >/dev/null
else
  kpackagetool6 --type KWin/Script -i "$SRC/kwin-script" >/dev/null
fi
kwriteconfig6 --file kwinrc --group Plugins --key "${KWIN_SCRIPT_ID}Enabled" true

# 3b. KWin effect: show our overlay/picker without the window open/close animation
if kpackagetool6 --type KWin/Effect -s "$KWIN_EFFECT_ID" >/dev/null 2>&1; then
  kpackagetool6 --type KWin/Effect -u "$SRC/kwin-effect" >/dev/null
else
  kpackagetool6 --type KWin/Effect -i "$SRC/kwin-effect" >/dev/null
fi
kwriteconfig6 --file kwinrc --group Plugins --key "${KWIN_EFFECT_ID}Enabled" true

# 4. user service (Plasma session only; not started in gamescope/Game Mode)
cat > "$UNIT" <<EOF
[Unit]
Description=snapshot-kwin screenshot daemon
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Environment=PYTHONPATH=$SRC
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$PY_REAL -m snapshot_kwin.app
Restart=on-failure
RestartSec=1

[Install]
WantedBy=plasma-workspace.target
EOF

# 5. hotkey entry point
cat > "$TRIGGER" <<EOF
#!/bin/sh
# Bound to a global shortcut: ask the resident daemon to capture.
exec busctl --user call $APP_ID /io/github/geojol/SnapshotKwin $APP_ID Capture
EOF
chmod 0755 "$TRIGGER"
printf '%s\n' '#!/bin/sh' 'exec busctl --user call io.github.geojol.SnapshotKwin /io/github/geojol/SnapshotKwin io.github.geojol.SnapshotKwin ShowHistory' > "$HISTORY"
chmod 0755 "$HISTORY"

# visible launcher: find it in the app menu / KRunner ("snapshot" or "截图");
# right-click actions open the history and turn the daemon on and off
cat > "$LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=snapshot-kwin 截图
Comment=Meta+Alt+1 截图 · Meta+Shift+V 剪贴板历史
Exec=$TRIGGER
Icon=applets-screenshooter
Keywords=screenshot;snapshot;clipboard;截图;剪贴板;
Actions=History;Start;Stop;

[Desktop Action History]
Name=剪贴板历史
Exec=$BIN/snapshot-kwin-history

[Desktop Action Start]
Name=开启(常驻服务)
Exec=systemctl --user enable --now snapshot-kwin.service

[Desktop Action Stop]
Name=关闭(停止并取消开机启动)
Exec=systemctl --user disable --now snapshot-kwin.service
EOF

# hidden shortcut entries (kglobalaccel components) + silent shot CLI, then take
# every screenshot key from Spectacle (it keeps recording): tools/take-over-shortcuts
for kind in capture history; do
  cat > "$DATA_HOME/applications/snapshot-kwin-$kind.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=snapshot-kwin $( [ $kind = capture ] && echo 截图 || echo 剪贴板历史 )
Exec=$BIN/snapshot-kwin-$kind
NoDisplay=true
EOF
done
ln -sfn "$SRC/tools/snapshot-kwin-shot" "$SHOT"
python3 "$SRC/tools/take-over-shortcuts" || echo "warning: shortcuts not taken over (no Plasma session?)" >&2

# terminal paste helper (bind Meta+V in the terminal to it, e.g. via xremap)
ln -sfn "$SRC/tools/term-paste" "$TERM_PASTE"

systemctl --user daemon-reload
systemctl --user enable snapshot-kwin.service >/dev/null
systemctl --user restart snapshot-kwin.service
# reload KWin scripts so the window feed starts now
busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1 || true
busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting start >/dev/null 2>&1 || true
busctl --user call org.kde.KWin /Effects org.kde.kwin.Effects reconfigureEffect s "$KWIN_EFFECT_ID" >/dev/null 2>&1 \
  || busctl --user call org.kde.KWin /Effects org.kde.kwin.Effects loadEffect s "$KWIN_EFFECT_ID" >/dev/null 2>&1 || true
echo "snapshot-kwin installed: Meta+Alt+1 / Print / Meta+Shift+Print capture, Meta+Shift+V history, snapshot-kwin-shot for scripts"
