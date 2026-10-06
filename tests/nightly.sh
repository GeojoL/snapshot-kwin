#!/usr/bin/env bash
# Nightly: unit tests + isolated end-to-end tests. Never touches the live desktop.
# Writes a dated log and latest.txt under ${XDG_STATE_HOME:-~/.local/state}/snapshot-kwin/e2e/.
set -uo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
OUT="${XDG_STATE_HOME:-$HOME/.local/state}/snapshot-kwin/e2e"
mkdir -p "$OUT"
LOG="$OUT/$(date +%Y%m%d-%H%M%S).log"
{
  echo "snapshot-kwin nightly $(date -Is) @ $(git -C "$SRC" rev-parse --short HEAD 2>/dev/null)"
  python3 -m unittest discover -s "$SRC/tests" 2>&1 | tail -3
  unit=${PIPESTATUS[0]}
  timeout 600 "$SRC/tests/e2e/run.sh" 2>&1 | grep -E '^(PASS|FAIL|e2e:|long capture)'
  e2e=${PIPESTATUS[0]}
  echo "unit_rc=$unit e2e_rc=$e2e"
} >"$LOG" 2>&1
cp "$LOG" "$OUT/latest.txt"
ls -1t "$OUT"/2*.log 2>/dev/null | tail -n +31 | xargs -r rm -f   # keep 30 runs
if grep -q 'unit_rc=0 e2e_rc=0' "$LOG"; then exit 0; fi
command -v notify-send >/dev/null && notify-send -a snapshot-kwin "snapshot-kwin 夜间测试失败" "详见 $LOG"
exit 1
