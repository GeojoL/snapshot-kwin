#!/usr/bin/env bash
# Runs inside the private D-Bus session started by run.sh.
set -uo pipefail
SOCK="skw-e2e-$$"
kwin_wayland --virtual --width 1600 --height 1000 --socket "$SOCK" >"$SKW_WORK/kwin.log" 2>&1 &
KWIN=$!
for _ in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/$SOCK" ] && break; sleep 0.1; done
export WAYLAND_DISPLAY="$SOCK" QT_QPA_PLATFORM=wayland GDK_BACKEND=wayland
unset DISPLAY
A=io.github.geojol.SnapshotKwin; P=/io/github/geojol/SnapshotKwin
call() { busctl --user call -- "$A" "$P" "$A" "$@"; }
pass=0; fail=0
check() { if eval "$2"; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1"; fail=$((fail+1)); fi; }
png_size() { python3 -c "import struct,sys;d=open(sys.argv[1],'rb').read(24);print('%dx%d'%struct.unpack('>II',d[16:24]))" "$1"; }
newest() { ls -t "$XDG_STATE_HOME"/snapshot-kwin/clipboard/*.png 2>/dev/null | head -1; }

# an old history entry with a long unbroken token: it must not widen the picker
mkdir -p "$XDG_STATE_HOME/snapshot-kwin/clipboard"
"$SKW_PY" -c "import json,sys,time;t='x'*300;json.dump([{'id':'longtoken','ts':time.time()-3600,'kind':'text','hash':'longtoken','text':t}],open(sys.argv[1],'w'))" "$XDG_STATE_HOME/snapshot-kwin/clipboard/index.json"
PYTHONPATH="$SKW_SRC" "$SKW_PY" -m snapshot_kwin.app >"$SKW_WORK/daemon.log" 2>&1 &
DAEMON=$!
for _ in $(seq 50); do busctl --user status "$A" >/dev/null 2>&1 && break; sleep 0.1; done
sleep 1

# 1. region capture
call Capture; sleep 0.6; call TestSelect iiii 100 100 400 300; sleep 0.8
check "region 400x300" '[ "$(png_size "$(newest)")" = 400x300 ]'

# 2. click a window -> its frame size
ghostty --title=e2e-win -e sleep 60 >/dev/null 2>&1 & GW=$!; sleep 3
call Capture; sleep 0.6; call TestClick ii 800 500; sleep 0.8
check "window click captured something" '[ -n "$(newest)" ] && [ "$(png_size "$(newest)")" != 400x300 ]'
kill $GW 2>/dev/null

# 3. long capture of a self-scrolling terminal (no input)
cat > "$SKW_WORK/scroll.sh" <<'S'
#!/bin/bash
for i in $(seq 1 60); do printf 'line %04d %s\n' $i "$(printf 'lorem ipsum %.0s' $(seq 1 $((i%5+1))))"; done
sleep 2
for i in $(seq 61 400); do printf 'line %04d %s\n' $i "$(printf 'lorem ipsum %.0s' $(seq 1 $((i%5+1))))"; [ $((i%4)) = 0 ] && sleep 0.12; done
sleep 30
S
chmod +x "$SKW_WORK/scroll.sh"
ghostty --title=e2e-long --window-width=100 --window-height=40 -e "$SKW_WORK/scroll.sh" >/dev/null 2>&1 & GL=$!
sleep 1.5
call TestLong iiiii 20 60 800 600 -1
for _ in $(seq 60); do grep -q 'long capture done\|long capture failed' "$SKW_WORK/daemon.log" && break; sleep 1; done
LONG="$(newest)"
check "long capture taller than one screen" '[ -n "$LONG" ] && [ "$(png_size "$LONG" | cut -dx -f2)" -gt 900 ]'
kill $GL 2>/dev/null

# 4. editor: open newest image, add an arrow, Enter -> new history image
before=$(ls "$XDG_STATE_HOME"/snapshot-kwin/clipboard/*.png 2>/dev/null | wc -l)
call TestEditLatest; sleep 1; call TestEditorArrowAndFinish; sleep 1
after=$(ls "$XDG_STATE_HOME"/snapshot-kwin/clipboard/*.png 2>/dev/null | wc -l)
check "editor result recorded" '[ "$after" -gt "$before" ]'

# 5. clipboard history: open, raise when already open, close when another window
#    takes focus, reopen as a fresh window
nlog() { grep -c "$1" "$SKW_WORK/daemon.log"; }
call ShowHistory; sleep 1
check "history opened" '[ "$(nlog "history opened")" -ge 1 ]'
call ShowHistory; sleep 0.5
check "history raised when already open" '[ "$(nlog "history raised")" -ge 1 ]'
ghostty --title=e2e-focus -e sleep 60 >/dev/null 2>&1 & GF=$!; sleep 3
check "history closed when focus moves away" '[ "$(nlog "history closed (focus lost)")" -ge 1 ]'
call ShowHistory; sleep 1
check "history reopens as a new window" '[ "$(nlog "history opened")" -ge 2 ]'
call TestHideHistory; sleep 0.5
check "history closed by hook" '[ "$(nlog "history closed (test)")" -ge 1 ]'
kill $GF 2>/dev/null

# 6. picker: confirming an older entry makes it the clipboard content and the
#    newest history entry; geometry reported by KWin is restored on reopen
IDX="$XDG_STATE_HOME/snapshot-kwin/clipboard/index.json"
second=$("$SKW_PY" -c "import json,sys;print(json.load(open(sys.argv[1]))[1]['hash'])" "$IDX")
call ShowHistory; sleep 1
call TestConfirmRow i 1; sleep 1
check "confirmed entry is the clipboard content" '[ "$(wl-paste --no-newline --type image/png | sha256sum | cut -d" " -f1)" = "$second" ]'
check "confirmed entry moved to the top" '[ "$("$SKW_PY" -c "import json,sys;print(json.load(open(sys.argv[1]))[0][\"hash\"])" "$IDX")" = "$second" ]'
call ShowHistory; sleep 1
call PickerGeometry s '{"x":120,"y":90,"w":520,"h":420}'; sleep 0.3
call TestHideHistory; sleep 0.5
check "picker geometry saved" 'grep -q "\"x\": 120" "$XDG_STATE_HOME/snapshot-kwin/picker.json"'
call ShowHistory; sleep 1.5
check "picker reopened where it was left (long text does not widen it)" '[ "$(nlog "picker geometry 120,90 520x420")" -ge 2 ]'
call TestHideHistory; sleep 0.5

# 7. text copied by another client after our own capture is still recorded,
#    without Klipper (the Plasma clipboard applet may be disabled)
#    Another window has the keyboard focus, as on a real desktop.
call Capture; sleep 0.6; call TestSelect iiii 50 50 200 120; sleep 0.8
ghostty --title=e2e-focus2 -e sleep 60 >/dev/null 2>&1 & GF2=$!; sleep 3
printf 'e2e-copied-text' | wl-copy; sleep 0.5
sleep 1.5  # no Klipper here: only the daemon's own clipboard watch can record it
check "text copied elsewhere after a capture is recorded" '"$SKW_PY" -c "import json,sys;d=json.load(open(sys.argv[1]));sys.exit(d[0].get(\"text\")!=\"e2e-copied-text\")" "$XDG_STATE_HOME/snapshot-kwin/clipboard/index.json"'
# the watcher is restarted if it dies: kill it, copy again, still recorded
# (only this session's watcher: the PID namespace is shared with the desktop)
wpid=$(grep -o "clipboard watch started (wl-paste --watch, pid [0-9]*" "$SKW_WORK/daemon.log" | tail -1 | grep -o "[0-9]*$")
[ -n "$wpid" ] && kill "$wpid"; sleep 2.5
printf 'e2e-after-restart' | wl-copy; sleep 1.5
check "clipboard watch restarts after being killed" '"$SKW_PY" -c "import json,sys;d=json.load(open(sys.argv[1]));sys.exit(d[0].get(\"text\")!=\"e2e-after-restart\")" "$XDG_STATE_HOME/snapshot-kwin/clipboard/index.json"'
kill $GF2 2>/dev/null

[ -n "${LONG:-}" ] && cp "$LONG" "${SKW_KEEP:-/dev/null}" 2>/dev/null || true
kill $DAEMON $KWIN 2>/dev/null
# Services D-Bus-activated on the private bus (portal, ksecretd, ...) outlive it;
# end every process attached to this bus so nothing is left behind.
for env in /proc/[0-9]*/environ; do
  pid=${env#/proc/}; pid=${pid%/environ}
  [ "$pid" = "$$" ] && continue
  grep -qz "^DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS\$" "$env" 2>/dev/null && kill "$pid" 2>/dev/null
done
sleep 0.5
[ -n "${SKW_LOG_OUT:-}" ] && cp "$SKW_WORK/daemon.log" "$SKW_LOG_OUT"
echo "e2e: $pass passed, $fail failed"
grep -oE 'long capture done.*' "$SKW_WORK/daemon.log" | cut -c1-120
[ "$fail" -eq 0 ]
