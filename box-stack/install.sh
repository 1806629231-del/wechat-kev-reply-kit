#!/usr/bin/env bash
#
# install.sh — heal the WeChat box stack after a box reset.
#
# Ensures Tailscale (CLI + daemon), proxychains4 and the domestic SOCKS
# configuration exist, installs the Tailscale watchdog, pins WX_DISPLAY and
# optionally runs the slim workspace bootstrap.
#
# Usage:
#   install.sh [--check] [--force] [--secrets FILE] [--no-bootstrap]
#
# Idempotent. Requires root or passwordless sudo for install mode.
# It never creates ~/.wx_proxy_enabled and never prints secret values.

set -euo pipefail

PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CHECK_MODE=0
FORCE=0
RUN_BOOTSTRAP=1

SOCKS_ENV="${SOCKS_ENV:-$HOME/secrets/phone-socks.env}"
TS_STATE_DIR="${TS_STATE_DIR:-$HOME/tailscale}"
TS_SOCKET="${TS_SOCKET:-/run/tailscale/tailscaled.sock}"
TS_SOCK_DIR="$(dirname "$TS_SOCKET")"
CONF="${CONF:-/etc/proxychains-wx.conf}"

WATCHDOG="$HOME/bin/ts-watchdog.sh"
BOOTSTRAP="$HOME/bin/bootstrap-wx.sh"
WX_ENV_DIR="$HOME/.config/wechat-box"
WX_ENV="$WX_ENV_DIR/env"
WATCHDOG_ENV="$WX_ENV_DIR/watchdog.env"

PHONE_SOCKS_HOST=""
PHONE_SOCKS_PORT=""
PHONE_SOCKS_USER=""
PHONE_SOCKS_PASS=""

usage() {
  cat <<'EOF'
Usage: install.sh [options]

Options:
  --check            Read-only checks. Prints PASS/FAIL/WARN; changes nothing.
  --force            Rewrite the pinned display env and proxychains config.
  --secrets FILE     Secrets file (default: $SOCKS_ENV or $HOME/secrets/phone-socks.env)
  --no-bootstrap     Install bootstrap-wx.sh but do not run it.
  -h, --help         Show this help.

Environment:
  SOCKS_ENV          Secrets file (default: $HOME/secrets/phone-socks.env)
  TS_STATE_DIR       Tailscale state dir (default: $HOME/tailscale)
  TS_SOCKET          tailscaled socket (default: /run/tailscale/tailscaled.sock)
  CONF               proxychains4 config (default: /etc/proxychains-wx.conf)
  WX_DISPLAY         Display to pin in ~/.config/wechat-box/env (default: :2)
  BOX_TS_IP_PREFIX   Tailscale IP prefix for the watchdog (default: 100.)
  MIN_GAP            Watchdog restart cooldown seconds (default: 600)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_MODE=1 ;;
    --force) FORCE=1 ;;
    --no-bootstrap) RUN_BOOTSTRAP=0 ;;
    --secrets)
      [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 1; }
      SOCKS_ENV="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
if [[ $EUID -eq 0 ]]; then
  SUDO=""
  REAL_USER="${SUDO_USER:-root}"
else
  SUDO="sudo -n"
  REAL_USER="$(id -un)"
fi
REAL_GROUP="$(id -gn "$REAL_USER" 2>/dev/null || id -gn)"

run_root() {
  if [[ -n "$SUDO" ]]; then
    $SUDO "$@"
  else
    "$@"
  fi
}

can_sudo() {
  [[ $EUID -eq 0 ]] || sudo -n true 2>/dev/null
}

# Parse the secrets file without executing it. Prefer PHONE_SOCKS_* names,
# fall back to legacy SOCKS_* names.
load_secrets() {
  PHONE_SOCKS_HOST=""
  PHONE_SOCKS_PORT=""
  PHONE_SOCKS_USER=""
  PHONE_SOCKS_PASS=""

  [[ -f "$SOCKS_ENV" ]] || return 1

  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == export\ * ]] && line="${line#export }"
    key="${line%%=*}"
    val="${line#*=}"
    key="${key//[[:space:]]/}"
    val="${val#\"}"; val="${val%\"}"
    val="${val#\'}"; val="${val%\'}"
    case "$key" in
      PHONE_SOCKS_HOST) PHONE_SOCKS_HOST="$val" ;;
      PHONE_SOCKS_PORT) PHONE_SOCKS_PORT="$val" ;;
      PHONE_SOCKS_USER) PHONE_SOCKS_USER="$val" ;;
      PHONE_SOCKS_PASS) PHONE_SOCKS_PASS="$val" ;;
      SOCKS_HOST) [[ -z "$PHONE_SOCKS_HOST" ]] && PHONE_SOCKS_HOST="$val" ;;
      SOCKS_PORT) [[ -z "$PHONE_SOCKS_PORT" ]] && PHONE_SOCKS_PORT="$val" ;;
      SOCKS_USER) [[ -z "$PHONE_SOCKS_USER" ]] && PHONE_SOCKS_USER="$val" ;;
      SOCKS_PASS) [[ -z "$PHONE_SOCKS_PASS" ]] && PHONE_SOCKS_PASS="$val" ;;
    esac
  done <"$SOCKS_ENV"

  PHONE_SOCKS_PORT="${PHONE_SOCKS_PORT:-1080}"
  [[ -n "$PHONE_SOCKS_HOST" && "$PHONE_SOCKS_HOST" != *"<"* ]] || return 1
  [[ -n "$PHONE_SOCKS_USER" && "$PHONE_SOCKS_USER" != *"<"* ]] || return 1
  [[ -n "$PHONE_SOCKS_PASS" && "$PHONE_SOCKS_PASS" != *"<"* ]] || return 1
  [[ "$PHONE_SOCKS_PORT" =~ ^[0-9]+$ ]] || return 1
  return 0
}

socks_curl() {
  local url="$1"
  curl -sS -L -m 20 -K - <<EOF
socks5-hostname = "${PHONE_SOCKS_HOST}:${PHONE_SOCKS_PORT}"
proxy-user = "${PHONE_SOCKS_USER}:${PHONE_SOCKS_PASS}"
url = "${url}"
EOF
}

socks_cn_ok() {
  local out
  out="$(socks_curl "https://myip.ipip.net" 2>/dev/null || true)"
  printf '%s' "$out" | grep -q '中国' && return 0

  out="$(socks_curl "https://ipinfo.io/country" 2>/dev/null || true)"
  [[ "$(printf '%s' "$out" | tr -d '[:space:]')" == "CN" ]]
}

# ---------------------------------------------------------------------------
# Read-only checks
# ---------------------------------------------------------------------------
run_check() {
  local fail=0
  echo "===== wechat-box-stack-heal check ====="

  if command -v tailscale >/dev/null 2>&1; then
    echo "PASS tailscale CLI installed"
  else
    echo "FAIL tailscale CLI missing"
    fail=1
  fi

  if pgrep -x tailscaled >/dev/null 2>&1; then
    echo "PASS tailscaled process running"
  else
    echo "FAIL tailscaled process not running"
    fail=1
  fi

  if [[ -S "$TS_SOCKET" ]] && command -v tailscale >/dev/null 2>&1; then
    local ts_out
    ts_out="$(timeout 10 tailscale --socket="$TS_SOCKET" status 2>&1 || true)"
    if [[ -n "$ts_out" ]] &&
       ! printf '%s' "$ts_out" | grep -qiE 'failed to connect|connection refused|no such file|timed out'; then
      echo "PASS Tailscale local API responds"
      if printf '%s' "$ts_out" | grep -qiE 'logged out|not logged in'; then
        echo "WARN Tailscale node is logged out; run: sudo tailscale --socket=$TS_SOCKET up"
      fi
    else
      echo "FAIL Tailscale local API not reachable via $TS_SOCKET"
      fail=1
    fi
  else
    echo "FAIL Tailscale local API not reachable via $TS_SOCKET"
    fail=1
  fi

  command -v proxychains4 >/dev/null 2>&1 \
    && echo "PASS proxychains4 installed" \
    || { echo "FAIL proxychains4 missing"; fail=1; }

  command -v curl >/dev/null 2>&1 \
    && echo "PASS curl installed" \
    || { echo "FAIL curl missing"; fail=1; }

  local have_secrets=0
  if [[ -f "$SOCKS_ENV" ]]; then
    local smode
    smode="$(stat -c '%a' "$SOCKS_ENV" 2>/dev/null || echo missing)"
    if [[ "$smode" == "600" ]]; then
      echo "PASS secrets file mode 600"
    else
      echo "WARN secrets file mode is $smode (expected 600)"
    fi
    if load_secrets; then
      have_secrets=1
      echo "PASS secrets file is filled"
    else
      echo "WARN secrets file present but not filled; SOCKS checks skipped"
    fi
  else
    echo "WARN secrets file missing ($SOCKS_ENV); SOCKS checks skipped"
  fi

  if [[ "$have_secrets" -eq 1 ]]; then
    if curl -s -m 8 "http://${PHONE_SOCKS_HOST}:8765/status" 2>/dev/null | grep -q '"v"' ||
       curl -s -m 8 "http://${PHONE_SOCKS_HOST}:8766/status" 2>/dev/null | grep -q '"v"'; then
      echo "PASS phone AutoX /status reachable"
    else
      echo "WARN phone AutoX /status unreachable (tailnet or app down)"
    fi

    if socks_cn_ok; then
      echo "PASS SOCKS exit is in mainland China"
    else
      echo "FAIL SOCKS exit check failed (proxy auth, egress or network)"
      fail=1
    fi
  else
    echo "WARN phone AutoX /status skipped (no usable secrets)"
  fi

  if [[ -f "$CONF" ]]; then
    echo "PASS proxychains config exists ($CONF)"
    local socks_line=""
    if [[ -r "$CONF" ]]; then
      socks_line="$(grep -E '^[[:space:]]*socks5[[:space:]]' "$CONF" | head -n1 || true)"
    elif can_sudo; then
      socks_line="$(sudo -n grep -E '^[[:space:]]*socks5[[:space:]]' "$CONF" | head -n1 || true)"
    fi
    if [[ -n "$socks_line" ]]; then
      echo "PASS proxychains config has a socks5 proxy line"
    else
      echo "WARN could not read/parse the socks5 line from proxychains config"
    fi
  else
    echo "FAIL proxychains config missing ($CONF)"
    fail=1
  fi

  if [[ -f "$WX_ENV" ]] && grep -q '^[[:space:]]*WX_DISPLAY=' "$WX_ENV"; then
    echo "PASS WX_DISPLAY pinned in $WX_ENV"
  else
    echo "FAIL WX_DISPLAY file missing or incomplete ($WX_ENV)"
    fail=1
  fi

  if [[ -x "$WATCHDOG" ]]; then
    echo "PASS ts-watchdog installed ($WATCHDOG)"
  else
    echo "FAIL ts-watchdog not installed ($WATCHDOG)"
    fail=1
  fi

  if [[ -x "$BOOTSTRAP" ]]; then
    echo "PASS bootstrap-wx installed ($BOOTSTRAP)"
  else
    echo "WARN bootstrap-wx not installed ($BOOTSTRAP)"
  fi

  if [[ $fail -ne 0 ]]; then
    echo "RESULT: FAIL"
    return 1
  fi
  echo "RESULT: PASS"
  return 0
}

if [[ $CHECK_MODE -eq 1 ]]; then
  run_check
  exit $?
fi

# ---------------------------------------------------------------------------
# Install mode
# ---------------------------------------------------------------------------
if ! can_sudo; then
  echo "ERROR: install mode needs root or passwordless sudo (sudo -n)." >&2
  echo "       Re-run as root or configure sudoers for this user." >&2
  exit 1
fi

install_pkgs() {
  local missing=() p
  for p in "$@"; do
    dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
  done
  [[ ${#missing[@]} -eq 0 ]] && return 0

  echo "Installing apt packages: ${missing[*]}"
  run_root apt-get update -y >/dev/null 2>&1 || true
  run_root apt-get install -y "${missing[@]}"
}

# 1. Base tools.
install_pkgs proxychains4 curl

# 2. Tailscale CLI + daemon.
if ! command -v tailscale >/dev/null 2>&1 || \
   { ! command -v tailscaled >/dev/null 2>&1 && [[ ! -x /usr/sbin/tailscaled ]]; }; then
  echo "Tailscale CLI or daemon missing; installing."
  run_root apt-get update -y >/dev/null 2>&1 || true
  if apt-cache show tailscale >/dev/null 2>&1; then
    run_root apt-get install -y tailscale
  else
    curl -fsSL https://tailscale.com/install.sh | run_root sh
  fi
fi

TS_BIN="$(command -v tailscaled 2>/dev/null || true)"
[[ -n "$TS_BIN" ]] || TS_BIN=/usr/sbin/tailscaled
if [[ ! -x "$TS_BIN" ]]; then
  echo "ERROR: tailscaled binary not found after install." >&2
  exit 1
fi

mkdir -p "$TS_STATE_DIR"
run_root mkdir -p "$TS_SOCK_DIR" /var/run/tailscale
# /var/run often aliases /run; ln -sfn then errors "same file" under set -e.
if [[ "$(readlink -f "$TS_SOCKET" 2>/dev/null || true)" != "$(readlink -f /var/run/tailscale/tailscaled.sock 2>/dev/null || true)" ]]; then
  run_root ln -sfn "$TS_SOCKET" /var/run/tailscale/tailscaled.sock || true
fi

if pgrep -x tailscaled >/dev/null 2>&1; then
  echo "tailscaled already running."
else
  echo "Starting tailscaled (state: $TS_STATE_DIR)."
  run_root nohup "$TS_BIN" --state="$TS_STATE_DIR/tailscaled.state" \
    --socket="$TS_SOCKET" --statedir="$TS_STATE_DIR" >>/tmp/tsd.log 2>&1 &
  sleep 3
fi

if [[ ! -f "$TS_STATE_DIR/tailscaled.state" ]]; then
  echo "WARN: Tailscale state is missing. If the node is not logged in, run once:"
  echo "      sudo tailscale --socket=$TS_SOCKET up"
fi

# 3. Secrets + proxychains config.
if [[ ! -f "$SOCKS_ENV" ]]; then
  mkdir -p "$(dirname "$SOCKS_ENV")"
  (
    umask 077
    cat >"$SOCKS_ENV" <<'EOF'
# WeChat domestic exit SOCKS5 proxy secrets.
# Fill in real values, then rerun install.sh.
# The proxy host is the phone/PC Tailscale IP, reached over the tailnet.
PHONE_SOCKS_HOST=<PHONE_TS_IP>
PHONE_SOCKS_PORT=1080
PHONE_SOCKS_USER=<SOCKS_USER>
PHONE_SOCKS_PASS=<SOCKS_PASS>

# Legacy aliases are also accepted:
#   SOCKS_HOST / SOCKS_PORT / SOCKS_USER / SOCKS_PASS
EOF
  )
  chmod 600 "$SOCKS_ENV"
  echo "Created a secrets template at: $SOCKS_ENV"
  echo "Fill in PHONE_SOCKS_HOST / PHONE_SOCKS_USER / PHONE_SOCKS_PASS, then rerun."
  exit 2
fi

smode="$(stat -c '%a' "$SOCKS_ENV" 2>/dev/null || echo 000)"
if [[ "$smode" != "600" ]]; then
  echo "WARN: $SOCKS_ENV has mode $smode; setting 600."
  chmod 600 "$SOCKS_ENV"
fi

if ! load_secrets; then
  echo "ERROR: secrets in $SOCKS_ENV are missing or still contain placeholders." >&2
  echo "       Edit that file and rerun." >&2
  exit 2
fi

CONF_TMP="$(mktemp "${TMPDIR:-/tmp}/proxychains-wx.XXXXXX")"
trap 'rm -f "$CONF_TMP"' EXIT
(
  umask 077
  cat >"$CONF_TMP" <<EOF
# proxychains4 config for the WeChat domestic exit.
# Generated by wechat-box-stack-heal install.sh — regenerate instead of editing.
strict_chain
proxy_dns
remote_dns_subnet 224
tcp_read_time_out 15000
tcp_connect_time_out 8000
localnet 127.0.0.0/255.0.0.0

# Do not exclude the Tailscale CGNAT range here. The SOCKS host is a tailnet
# peer and proxychains always reaches the proxy hop directly.

[ProxyList]
socks5 ${PHONE_SOCKS_HOST} ${PHONE_SOCKS_PORT} ${PHONE_SOCKS_USER} ${PHONE_SOCKS_PASS}
EOF
)

if [[ -f "$CONF" ]] && cmp -s "$CONF_TMP" "$CONF" 2>/dev/null; then
  echo "proxychains config unchanged ($CONF)."
else
  run_root install -m 640 -o root -g "$REAL_GROUP" "$CONF_TMP" "$CONF"
  echo "Wrote proxychains config: $CONF"
fi
rm -f "$CONF_TMP"
trap - EXIT

# 4. Watchdog config + scripts.
mkdir -p "$WX_ENV_DIR" "$HOME/bin"
chmod 700 "$WX_ENV_DIR"

WATCHDOG_TMP="$(mktemp "${TMPDIR:-/tmp}/watchdog-env.XXXXXX")"
(
  umask 077
  {
    echo "# Generated by wechat-box-stack-heal install.sh (mode 600)."
    printf 'PHONE_TS_IP=%q\n' "$PHONE_SOCKS_HOST"
    printf 'BOX_TS_IP_PREFIX=%q\n' "${BOX_TS_IP_PREFIX:-100.}"
    printf 'SOCKS_ENV=%q\n' "$SOCKS_ENV"
    printf 'TS_STATE_DIR=%q\n' "$TS_STATE_DIR"
    printf 'TS_SOCKET=%q\n' "$TS_SOCKET"
    printf 'MIN_GAP=%q\n' "${MIN_GAP:-600}"
  } >"$WATCHDOG_TMP"
)
install -m 600 "$WATCHDOG_TMP" "$WATCHDOG_ENV"
rm -f "$WATCHDOG_TMP"

install -m 755 "$PACK_DIR/scripts/ts-watchdog.sh" "$WATCHDOG"
echo "Installed watchdog: $WATCHDOG"

# 5. Pin the WeChat desktop.
if [[ -f "$WX_ENV" && $FORCE -eq 0 ]]; then
  if ! grep -q '^[[:space:]]*WX_DISPLAY=' "$WX_ENV"; then
    printf '\n# Pinned by wechat-box-stack-heal.\nWX_DISPLAY=%q\n' "${WX_DISPLAY:-:2}" >>"$WX_ENV"
  fi
  chmod 600 "$WX_ENV"
  echo "Kept existing display env: $WX_ENV"
else
  (
    umask 077
    cat >"$WX_ENV" <<EOF
# Sourced by agents and bootstrap-wx.sh before starting WeChat helpers.
# Change this if xdpyinfo shows a different live desktop (for example :5).
WX_DISPLAY=${WX_DISPLAY:-:2}
EOF
  )
  chmod 600 "$WX_ENV"
  echo "Wrote display env: $WX_ENV (WX_DISPLAY=${WX_DISPLAY:-:2})"
fi

# 6. Slim workspace bootstrap.
install -m 755 "$PACK_DIR/scripts/bootstrap-wx.sh" "$BOOTSTRAP"
echo "Installed bootstrap: $BOOTSTRAP"

if [[ $RUN_BOOTSTRAP -eq 1 ]]; then
  echo "Running workspace bootstrap."
  bash "$BOOTSTRAP" || echo "WARN: bootstrap reported issues (see output above)."
else
  echo "Skipped bootstrap run (--no-bootstrap). Run: $BOOTSTRAP"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
cat <<EOF

--------------------------------------------------------------
Stack heal complete.
  tailscale : $TS_BIN (socket $TS_SOCKET)
  state     : $TS_STATE_DIR
  secrets   : $SOCKS_ENV (mode 600)
  conf      : $CONF
  watchdog  : $WATCHDOG
  display   : $WX_ENV (WX_DISPLAY=${WX_DISPLAY:-:2})
  bootstrap : $BOOTSTRAP

The proxy flag (~/.wx_proxy_enabled) is intentionally NOT created.
Enable the domestic exit only after the account is ready:
  touch ~/.wx_proxy_enabled

Start the watchdog in the background when you want auto-heal:
  nohup ~/bin/ts-watchdog.sh >/dev/null 2>&1 &
For boot-time healing, run the watchdog (and optionally bootstrap-wx.sh)
from systemd or cron. Do not reset the box for a repair this pack can make.
--------------------------------------------------------------
EOF
