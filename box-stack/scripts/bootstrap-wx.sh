#!/usr/bin/env bash
#
# bootstrap-wx.sh — generic, slim post-reset heal for a WeChat-bot workspace.
#
# It only fills in missing pieces and starts services that are not already
# running. It never kills a healthy process.
#
# Layout it understands (all optional):
#   ${WORKSPACE:-$HOME/wechat-bot or /workspace}
#     wxauto/   Python venv, mention_watch, task_worker
#     kev/      local kev service on port 8009
#
# Usage: bootstrap-wx.sh [--check-only]
#
# No secrets are read or printed.

set -uo pipefail

CHECK_ONLY=0
case "${1:-}" in
  --check-only) CHECK_ONLY=1 ;;
  -h|--help)
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  "") ;;
  *) echo "unknown option: $1" >&2; exit 1 ;;
esac

WORKSPACE="${WORKSPACE:-}"
if [[ -z "$WORKSPACE" ]]; then
  if [[ -d /workspace ]]; then
    WORKSPACE=/workspace
  else
    WORKSPACE="$HOME/wechat-bot"
  fi
fi

WX="$WORKSPACE/wxauto"
KEV="$WORKSPACE/kev"
KEV_URL="${KEV_URL:-http://127.0.0.1:8009}"
KEV_MODEL="${KEV_MODEL:-jaredpalmer/kev-0.8b}"
WX_PY_PKGS="${WX_PY_PKGS:-mss numpy pillow opencv-python-headless rapidocr_onnxruntime python-dotenv requests}"

# ---------------------------------------------------------------------------
# Desktop choice. The sandbox desktop slot can move after a desktop restart
# (for example :2 -> :5). Prefer WX_DISPLAY, then a recently used live display.
# ---------------------------------------------------------------------------
WX_ENV="$HOME/.config/wechat-box/env"
if [[ -z "${WX_DISPLAY:-}" && -r "$WX_ENV" ]]; then
  # shellcheck disable=SC1090
  . "$WX_ENV"
fi

if [[ -n "${WX_DISPLAY:-}" ]]; then
  DISPLAY="$WX_DISPLAY"
else
  DISPLAY=""
  for f in $(ls -t /tmp/sand-window-used.d/* 2>/dev/null); do
    d=":$(basename "$f")"
    if DISPLAY="$d" xdpyinfo >/dev/null 2>&1; then DISPLAY="$d"; break; fi
  done
  if [[ -z "$DISPLAY" ]]; then
    for d in :2 :1 :5 :0; do
      if DISPLAY="$d" xdpyinfo >/dev/null 2>&1; then DISPLAY="$d"; break; fi
    done
  fi
  DISPLAY="${DISPLAY:-:2}"
fi
export DISPLAY

declare -a SUMMARY
ok()   { SUMMARY+=("[OK]   $1"); }
fix()  { SUMMARY+=("[FIX]  $1"); }
bad()  { SUMMARY+=("[FAIL] $1"); }
skip() { SUMMARY+=("[SKIP] $1"); }

run() {
  [[ "$CHECK_ONLY" -eq 1 ]] && return 1
  "$@"
}

echo "Workspace: $WORKSPACE"
echo "Display:   $DISPLAY"

# ---------------------------------------------------------------------------
# 1. PulseAudio virtual microphone (only when the tools are installed)
# ---------------------------------------------------------------------------
if command -v pactl >/dev/null 2>&1; then
  if ! pactl info >/dev/null 2>&1; then
    run pulseaudio --start --exit-idle-time=-1 >/dev/null 2>&1 || true
    sleep 1
  fi
  if pactl info >/dev/null 2>&1; then
    if pactl list short sinks 2>/dev/null | grep -q vmic_sink; then
      ok "PulseAudio vmic_sink"
    elif run pactl load-module module-null-sink sink_name=vmic_sink rate=44100 channels=1 >/dev/null 2>&1; then
      fix "created PulseAudio vmic_sink"
    else
      bad "PulseAudio vmic_sink"
    fi
    if pactl list short sources 2>/dev/null | grep -q vmic; then
      ok "PulseAudio vmic source"
    elif run pactl load-module module-remap-source master=vmic_sink.monitor source_name=vmic rate=44100 channels=1 >/dev/null 2>&1; then
      fix "created PulseAudio vmic source"
    else
      bad "PulseAudio vmic source"
    fi
  else
    bad "PulseAudio not running"
  fi
else
  skip "pulseaudio tools not installed"
fi

# ---------------------------------------------------------------------------
# 2. wxauto Python venv (optional; skipped cleanly when wxauto is absent)
# ---------------------------------------------------------------------------
if [[ -d "$WX" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    bad "python3 missing (wxauto venv skipped)"
  else
    venv_ok=0
    if [[ -x "$WX/.venv/bin/python" ]] &&
       "$WX/.venv/bin/python" -c 'import mss, numpy, PIL, cv2, rapidocr_onnxruntime, dotenv, requests' >/dev/null 2>&1; then
      venv_ok=1
    fi

    if [[ "$venv_ok" -eq 1 ]]; then
      ok "wxauto .venv"
    elif [[ "$CHECK_ONLY" -eq 1 ]]; then
      bad "wxauto .venv missing or incomplete"
    else
      if (
        cd "$WX" || exit 1
        [[ -x .venv/bin/python ]] || python3 -m venv .venv || exit 1
        if [[ -f requirements.txt ]]; then
          .venv/bin/pip install -q -r requirements.txt
        else
          # shellcheck disable=SC2086
          .venv/bin/pip install -q $WX_PY_PKGS
        fi
      ) >/tmp/bootstrap_wx_pip.log 2>&1; then
        fix "rebuilt wxauto .venv"
      else
        bad "wxauto .venv (see /tmp/bootstrap_wx_pip.log)"
      fi
    fi
  fi
else
  skip "$WX not present"
fi

# ---------------------------------------------------------------------------
# 3. kev service on :8009 (optional; only when $WORKSPACE/kev exists)
# ---------------------------------------------------------------------------
kev_alive() {
  pgrep -f 'kev.serve' >/dev/null 2>&1 &&
    curl -s -o /dev/null -m 3 "$KEV_URL" 2>/dev/null
}

if [[ -d "$KEV" ]]; then
  if kev_alive; then
    ok "kev on $KEV_URL"
  elif [[ "$CHECK_ONLY" -eq 1 ]]; then
    bad "kev not running on $KEV_URL"
  elif ! command -v uv >/dev/null 2>&1; then
    bad "kev not running (uv is missing)"
  else
    (
      cd "$KEV" || exit 1
      nohup uv run --extra serve python -m kev.serve --run "$KEV_MODEL" --port 8009 \
        >>serve.log 2>&1 &
    )
    fix "starting kev on :8009"
    for _ in $(seq 1 20); do
      kev_alive && break
      sleep 2
    done
    if kev_alive; then
      ok "kev on $KEV_URL"
    else
      bad "kev did not come up (see $KEV/serve.log)"
    fi
  fi
else
  skip "$KEV not present"
fi

# ---------------------------------------------------------------------------
# 4. Bot workers. Start only when the corresponding script exists, and only
#    when the process is not already running. Never kill a healthy process.
# ---------------------------------------------------------------------------
start_worker() {
  local name="$1" pattern="$2" launch="$3" py="$4"

  if pgrep -f "$pattern" >/dev/null 2>&1; then
    ok "$name already running"
    return
  fi

  if [[ -n "$launch" && -f "$launch" ]]; then
    if [[ "$CHECK_ONLY" -eq 1 ]]; then
      bad "$name not running"
      return
    fi
    ( cd "$WX" && DISPLAY="$DISPLAY" nohup bash "$launch" >>"$name.out" 2>&1 & )
    fix "started $name"
    return
  fi

  if [[ -n "$py" && -f "$py" && -x "$WX/.venv/bin/python" ]]; then
    if [[ "$CHECK_ONLY" -eq 1 ]]; then
      bad "$name not running"
      return
    fi
    ( cd "$WX" && DISPLAY="$DISPLAY" nohup "$WX/.venv/bin/python" "$py" >>"$name.out" 2>&1 & )
    fix "started $name"
    return
  fi

  skip "$name launcher not found"
}

if [[ -d "$WX" ]]; then
  start_worker "mention_watch" "mention_watch.py" "$WX/mention_watch.launch.sh" "$WX/mention_watch.py"
  start_worker "task_worker" "task_worker.py" "$WX/task_worker.launch.sh" "$WX/task_worker.py"
else
  skip "mention_watch / task_worker (wxauto absent)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "===== WeChat box bootstrap $(date '+%F %T %Z') ====="
printf '%s\n' "${SUMMARY[@]}"

if printf '%s\n' "${SUMMARY[@]}" | grep -q '^\[FAIL\]'; then
  exit 1
fi
exit 0
