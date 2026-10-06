#!/usr/bin/env bash
# End-to-end tests in an isolated, invisible KWin session.
#
# Nothing touches the user's desktop: a nested `kwin_wayland --virtual` runs on
# its own D-Bus session bus, mount namespace and Wayland socket, with throwaway
# XDG config/state dirs, so the user's focus, input, clipboard, history and
# Flatpak document portal are never involved.
# Scenarios drive the daemon through its Test* D-Bus hooks; the long capture uses
# a terminal whose content scrolls by itself (no input injection at all).
#
#   tests/e2e/run.sh            run all scenarios, exit non-zero on failure
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
PY="${SNAPSHOT_KWIN_PYTHON:-$(readlink -f "$HOME/.local/libexec/snapshot-kwin/snapshot-kwin-python")}"
[ -x "$PY" ] || { echo "authorized interpreter missing; run ./install.sh first" >&2; exit 2; }
WORK="$(mktemp -d "${TMPDIR:-/tmp}/snapshot-kwin-e2e.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/config" "$WORK/state" "$WORK/runtime"
chmod 700 "$WORK/runtime"
# nested KWin config: our window feed script and no-animation effect enabled
cat > "$WORK/config/kwinrc" <<CFG
[Plugins]
snapshot-kwin-windowsEnabled=true
snapshot-kwin-noanimEnabled=true
CFG
export XDG_CONFIG_HOME="$WORK/config" XDG_STATE_HOME="$WORK/state"
export SKW_SRC="$SRC" SKW_PY="$PY" SKW_WORK="$WORK"
# The private bus shares XDG_RUNTIME_DIR with the desktop. A document portal
# activated on it lazily unmounts the desktop's $XDG_RUNTIME_DIR/doc
# (flatpak/xdg-desktop-portal#2140), after which no Flatpak app can start.
# A private mount namespace keeps the desktop's mounts out of its reach.
command -v bwrap >/dev/null || { echo "bwrap missing; refusing to run without mount isolation" >&2; exit 2; }
DOC="$XDG_RUNTIME_DIR/doc"
doc_mounted() { findmnt -n -t fuse.portal "$DOC" >/dev/null; }
doc_before=0; doc_mounted && doc_before=1
rc=0
bwrap --bind / / --dev-bind /dev /dev --die-with-parent -- \
  dbus-run-session -- bash "$SRC/tests/e2e/session.sh" || rc=$?
if [ "$doc_before" = 1 ] && ! doc_mounted; then
  echo "FAIL desktop document portal mount $DOC was lost; restore: systemctl --user restart xdg-document-portal.service" >&2
  rc=1
fi
exit "$rc"
