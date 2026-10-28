#!/bin/bash
# Use the live desktop display instead of assuming the old :2 slot.
if [ -z "${WX_DISPLAY:-}" ] && [ -f /workspace/wxauto/.wx_display ]; then
  # shellcheck disable=SC1091
  . /workspace/wxauto/.wx_display
fi
DISPLAY="${WX_DISPLAY:-}"
if [ -z "$DISPLAY" ]; then
  for f in $(ls -t /tmp/sand-window-used.d/* 2>/dev/null); do
    d=":$(basename "$f")"
    if DISPLAY="$d" xdpyinfo >/dev/null 2>&1; then DISPLAY="$d"; break; fi
  done
fi
if [ -z "$DISPLAY" ]; then
  for d in :2 :1 :5 :0; do
    if DISPLAY="$d" xdpyinfo >/dev/null 2>&1; then DISPLAY="$d"; break; fi
  done
fi
DISPLAY="${DISPLAY:-:2}"
cd /workspace/wxauto && DISPLAY="$DISPLAY" PATH=/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games \
  .venv/bin/python mention_watch.py --interval 3
