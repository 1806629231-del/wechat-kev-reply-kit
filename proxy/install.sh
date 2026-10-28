#!/usr/bin/env bash
#
# install.sh — route a box/cloud-hosted Linux WeChat through a domestic
# SOCKS5 proxy (a phone or PC reached over Tailscale) using proxychains4.
#
# Usage:
#   install.sh [--check] [--secrets FILE] [--conf FILE] [--launcher FILE]
#              [--flag FILE] [--wechat-bin PATH] [--force] [--quiet-ip]
#
# The proxy is DISABLED by default. It is only used once you create the
# flag file (see the summary at the end of a normal run).

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SECRETS="${SECRETS:-$HOME/secrets/wx-socks.env}"
CONF="/etc/proxychains-wx.conf"
LAUNCHER="$HOME/bin/wechat-launch.sh"
FLAG="$HOME/.wx_proxy_enabled"
WECHAT_BIN="/usr/bin/wechat"

CHECK_MODE=0
FORCE=0
QUIET_IP=0

usage() {
  cat <<'EOF'
Usage: install.sh [options]

Options:
  --check            Read-only checks; no files are changed. Exits non-zero on FAIL.
  --secrets FILE     Secrets file (default: $HOME/secrets/wx-socks.env)
  --conf FILE        proxychains4 config (default: /etc/proxychains-wx.conf)
  --launcher FILE    WeChat launcher (default: $HOME/bin/wechat-launch.sh)
  --flag FILE        Enable flag (default: $HOME/.wx_proxy_enabled)
  --wechat-bin PATH  WeChat binary (default: /usr/bin/wechat)
  --force            Overwrite an existing launcher
  --quiet-ip         Mask the public exit IP in --check output
  -h, --help         Show this help

Environment:
  EXPECT_EXIT_IP     (--check) fail if the authenticated exit IP differs
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_MODE=1 ;;
    --secrets) [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 1; }; SECRETS="$2"; shift ;;
    --conf) [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 1; }; CONF="$2"; shift ;;
    --launcher) [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 1; }; LAUNCHER="$2"; shift ;;
    --flag) [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 1; }; FLAG="$2"; shift ;;
    --wechat-bin) [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 1; }; WECHAT_BIN="$2"; shift ;;
    --force) FORCE=1 ;;
    --quiet-ip) QUIET_IP=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
if [[ $EUID -eq 0 ]]; then
  REAL_USER="${SUDO_USER:-root}"
else
  REAL_USER="$(id -un)"
fi
REAL_GROUP="$(id -gn "$REAL_USER" 2>/dev/null || id -gn)"

can_sudo() {
  sudo -n true 2>/dev/null
}

mask_text() {
  if [[ $QUIET_IP -eq 1 ]]; then
    printf '%s' "$1" | sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/<ip-masked>/g'
  else
    printf '%s' "$1"
  fi
}

# TCP connect test (5s) using bash /dev/tcp, with nc fallback.
tcp_reach() {
  local h="$1" p="$2"
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 bash -c "exec 3<>/dev/tcp/${h}/${p}" 2>/dev/null
  elif command -v nc >/dev/null 2>&1; then
    nc -z -w 5 "$h" "$p" >/dev/null 2>&1
  else
    return 1
  fi
}

# Run curl through the authenticated SOCKS5 proxy. Credentials are passed
# via a curl config on stdin (-K -) so they never show up in 'ps'.
socks5_auth_curl() {
  local url="$1"; shift
  curl "$@" -s -L -m 20 -K - <<EOF
socks5-hostname = "${PHONE_SOCKS_HOST}:${PHONE_SOCKS_PORT}"
proxy-user = "${PHONE_SOCKS_USER}:${PHONE_SOCKS_PASS}"
url = "${url}"
EOF
}

# Load secrets without sourcing (so the file cannot execute code).
# Prefers PHONE_* names, falls back to legacy SOCKS_* names.
load_secrets() {
  PHONE_SOCKS_HOST=""
  PHONE_SOCKS_PORT=""
  PHONE_SOCKS_USER=""
  PHONE_SOCKS_PASS=""

  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%$'\r'}"   # strip Windows CR
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
  done < "$SECRETS"

  PHONE_SOCKS_PORT="${PHONE_SOCKS_PORT:-1080}"

  if [[ -z "$PHONE_SOCKS_HOST" || "$PHONE_SOCKS_HOST" == *"<"* ||
        -z "$PHONE_SOCKS_USER"  || "$PHONE_SOCKS_USER"  == *"<"* ||
        -z "$PHONE_SOCKS_PASS"  || "$PHONE_SOCKS_PASS"  == *"<"* ]]; then
    return 1
  fi
  if [[ ! "$PHONE_SOCKS_PORT" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Install pieces
# ---------------------------------------------------------------------------
write_conf() {
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/proxychains-wx.XXXXXX")"

  # The password only ever lands in this umask-077 temp file, then into CONF.
  ( umask 077; cat > "$tmp" <<EOF
# proxychains4 configuration for the WeChat domestic exit.
# Generated by install.sh — regenerate rather than hand-editing.
strict_chain
proxy_dns
remote_dns_subnet 224
tcp_read_time_out 15000
tcp_connect_time_out 8000
localnet 127.0.0.0/255.0.0.0

# NOTE: we intentionally do NOT add 'localnet 100.64.0.0/255.192.0.0'
# (the Tailscale CGNAT range). The SOCKS5 proxy host is a Tailscale peer
# and is always reached directly by proxychains (the hop to the proxy host
# itself is never proxied), so no localnet exclusion is required. Adding it
# would wrongly bypass the proxy for the whole 100.64.0.0/10 range.

[ProxyList]
socks5 ${PHONE_SOCKS_HOST} ${PHONE_SOCKS_PORT} ${PHONE_SOCKS_USER} ${PHONE_SOCKS_PASS}
EOF
  )

  local needs_write=0
  if [[ -f "$CONF" ]]; then
    if cmp -s "$tmp" "$CONF" 2>/dev/null; then
      needs_write=0
    else
      needs_write=1
    fi
  else
    needs_write=1
  fi

  if [[ $needs_write -eq 0 ]]; then
    echo "CONF unchanged ($CONF); skipping write."
    rm -f "$tmp"
    return 0
  fi

  # Back up an existing but different file.
  if [[ -f "$CONF" ]]; then
    local bak
    bak="${CONF}.bak-$(date +%Y%m%d%H%M%S)"
    if [[ $EUID -eq 0 ]]; then
      cp -p "$CONF" "$bak"
    elif can_sudo; then
      sudo -n cp -p "$CONF" "$bak"
    else
      cp -p "$CONF" "$bak" 2>/dev/null || true
    fi
    echo "Backed up existing CONF to $bak"
  fi

  # Install with the requested ownership/mode.
  if [[ $EUID -eq 0 ]]; then
    install -m 640 -o root -g "$REAL_GROUP" "$tmp" "$CONF"
  elif can_sudo; then
    sudo -n install -m 640 -o root -g "$REAL_GROUP" "$tmp" "$CONF"
  else
    # Not root and no passwordless sudo: fall back to a user-owned 600 file.
    install -m 600 "$tmp" "$CONF" || {
      echo "ERROR: cannot write CONF ($CONF) without root/sudo." >&2
      echo "       Run with sudo, or pass --conf to a writable path." >&2
      rm -f "$tmp"
      exit 1
    }
  fi
  rm -f "$tmp"
  echo "Wrote CONF: $CONF"
}

write_launcher() {
  if [[ -e "$LAUNCHER" && $FORCE -eq 0 ]]; then
    echo "Launcher exists ($LAUNCHER); skipping (use --force to overwrite)."
    return 0
  fi

  mkdir -p "$(dirname "$LAUNCHER")"
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/wechat-launch.XXXXXX")"
  cat > "$tmp" <<EOF
#!/usr/bin/env bash
# WeChat launcher with optional domestic SOCKS5 exit via proxychains4.
# Generated by install.sh.
#
# strict_chain means that when the proxy is down WeChat gets NO network at
# all, rather than silently falling back to the box's direct IP.
set -euo pipefail
CONF='${CONF}'
WECHAT_BIN='${WECHAT_BIN}'
FLAG='${FLAG}'
if [[ -e "\$FLAG" ]]; then
  exec proxychains4 -q -f "\$CONF" "\$WECHAT_BIN" "\$@"
else
  exec "\$WECHAT_BIN" "\$@"
fi
EOF
  chmod 755 "$tmp"
  install -m 755 "$tmp" "$LAUNCHER" 2>/dev/null || mv -f "$tmp" "$LAUNCHER"
  rm -f "$tmp"
  echo "Wrote launcher: $LAUNCHER"
}

# ---------------------------------------------------------------------------
# Read-only checks (--check)
# ---------------------------------------------------------------------------
run_check() {
  set +e
  local fail=0

  # --- binaries ---
  if command -v proxychains4 >/dev/null 2>&1; then
    echo "PASS proxychains4 installed ($(command -v proxychains4))"
  else
    echo "FAIL proxychains4 not installed"
    fail=1
  fi

  if command -v curl >/dev/null 2>&1; then
    echo "PASS curl installed"
  else
    echo "FAIL curl not installed"
    fail=1
  fi

  if command -v python3 >/dev/null 2>&1; then
    echo "PASS python3 installed"
  else
    echo "WARN python3 not installed (country check degrades)"
  fi

  # --- secrets mode (read-only: just report) ---
  local smode
  smode="$(stat -c '%a' "$SECRETS" 2>/dev/null || echo 'missing')"
  if [[ "$smode" == "600" ]]; then
    echo "PASS secrets file mode 600 ($SECRETS)"
  else
    echo "WARN secrets file mode is ${smode}, expected 600 (run install to fix)"
  fi

  # --- CONF exists + socks5 line matches secrets (compared silently) ---
  if [[ -f "$CONF" ]]; then
    echo "PASS CONF exists ($CONF)"
  else
    echo "FAIL CONF missing ($CONF)"
    fail=1
  fi

  local conf_host="" conf_port="" conf_user="" socks_line=""
  if [[ -f "$CONF" ]]; then
    if [[ -r "$CONF" ]]; then
      socks_line="$(grep -E '^[[:space:]]*socks5[[:space:]]' "$CONF" | head -n1 || true)"
    elif can_sudo; then
      socks_line="$(sudo -n grep -E '^[[:space:]]*socks5[[:space:]]' "$CONF" | head -n1 || true)"
    fi
    if [[ -n "$socks_line" ]]; then
      read -r _ conf_host conf_port conf_user _ <<<"$socks_line"
      if [[ "$conf_host" == "$PHONE_SOCKS_HOST" ]]; then
        echo "PASS CONF socks5 host matches secrets host"
      else
        echo "FAIL CONF socks5 host does not match secrets host"
        fail=1
      fi
      if [[ "$conf_port" == "$PHONE_SOCKS_PORT" ]]; then
        echo "PASS CONF socks5 port matches secrets port"
      else
        echo "WARN CONF socks5 port differs from secrets port"
      fi
      if [[ "$conf_user" == "$PHONE_SOCKS_USER" ]]; then
        echo "PASS CONF socks5 user matches secrets user"
      else
        echo "WARN CONF socks5 user differs from secrets user"
      fi
    else
      echo "WARN could not read/parse the socks5 line from CONF"
    fi
  fi

  # --- tailscale peer reachability (tcp connect) ---
  if tcp_reach "$PHONE_SOCKS_HOST" "$PHONE_SOCKS_PORT"; then
    echo "PASS tailscale peer reachable (tcp ${PHONE_SOCKS_HOST}:${PHONE_SOCKS_PORT})"
  else
    echo "FAIL tailscale peer not reachable at ${PHONE_SOCKS_HOST}:${PHONE_SOCKS_PORT}"
    fail=1
  fi

  # --- authenticated exit (ipinfo + myip.ipip.net) ---
  local ipinfo_json="" ip="" country="" org=""
  if ipinfo_json="$(socks5_auth_curl "http://ipinfo.io/json")" && [[ -n "$ipinfo_json" ]]; then
    if command -v python3 >/dev/null 2>&1; then
      ip="$(printf '%s' "$ipinfo_json" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("ip",""))' 2>/dev/null || true)"
      country="$(printf '%s' "$ipinfo_json" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("country",""))' 2>/dev/null || true)"
      org="$(printf '%s' "$ipinfo_json" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("org",""))' 2>/dev/null || true)"
    else
      # grep fallback (no python3)
      ip="$(printf '%s' "$ipinfo_json" | sed -nE 's/.*"ip"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n1)"
      country="$(printf '%s' "$ipinfo_json" | sed -nE 's/.*"country"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n1)"
      org="$(printf '%s' "$ipinfo_json" | sed -nE 's/.*"org"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n1)"
    fi
    if [[ -z "$ip" ]]; then
      echo "FAIL authenticated exit: got no IP from ipinfo"
      fail=1
    else
      echo "INFO authenticated exit — ip=$(mask_text "$ip") country=${country:-?} org=${org:-?}"
      if [[ -z "$country" ]]; then
        echo "WARN could not determine exit country"
      elif [[ "$country" != "CN" ]]; then
        echo "FAIL authenticated exit country is ${country}, expected CN"
        fail=1
      else
        echo "PASS authenticated exit country is CN"
      fi
    fi
  else
    echo "FAIL authenticated exit request failed (proxy auth or network problem)"
    fail=1
  fi

  local myip
  if myip="$(socks5_auth_curl "https://myip.ipip.net")"; then
    echo "INFO myip.ipip.net says: $(mask_text "$(printf '%s' "$myip" | tr -d '\r\n')")"
  else
    echo "WARN myip.ipip.net request failed"
  fi

  # --- unauthenticated access must be refused ---
  if curl -s -m 20 --socks5-hostname "${PHONE_SOCKS_HOST}:${PHONE_SOCKS_PORT}" \
       "http://ipinfo.io/ip" >/dev/null 2>&1; then
    echo "FAIL unauthenticated SOCKS5 access succeeded (server must require auth)"
    fail=1
  else
    echo "PASS unauthenticated SOCKS5 access refused"
  fi

  # --- wrong password must be refused ---
  local wrongpass
  wrongpass="wrong-$(date +%s%N 2>/dev/null || date +%s)-${RANDOM}"
  if curl -s -m 20 -K - >/dev/null 2>&1 <<EOF
socks5-hostname = "${PHONE_SOCKS_HOST}:${PHONE_SOCKS_PORT}"
proxy-user = "${PHONE_SOCKS_USER}:${wrongpass}"
url = "http://ipinfo.io/ip"
EOF
  then
    echo "FAIL wrong password was accepted (server auth is broken)"
    fail=1
  else
    echo "PASS wrong password refused"
  fi

  # --- proxychains end-to-end equals the authenticated exit IP ---
  if [[ -n "$ip" ]]; then
    local e2e_ip=""
    if [[ -r "$CONF" ]]; then
      e2e_ip="$(proxychains4 -q -f "$CONF" curl -s -L -m 20 "http://ipinfo.io/ip" 2>/dev/null || true)"
    elif can_sudo; then
      echo "WARN CONF not readable as current user; retrying proxychains via sudo"
      e2e_ip="$(sudo -n proxychains4 -q -f "$CONF" curl -s -L -m 20 "http://ipinfo.io/ip" 2>/dev/null || true)"
    else
      echo "WARN CONF not readable and no sudo; skipping proxychains end-to-end"
    fi
    e2e_ip="$(printf '%s' "$e2e_ip" | tr -d '[:space:]')"
    if [[ -n "$e2e_ip" ]]; then
      if [[ "$e2e_ip" == "$ip" ]]; then
        echo "PASS proxychains end-to-end exit IP matches authenticated exit"
      else
        echo "FAIL proxychains end-to-end exit IP differs (proxychains=$(mask_text "$e2e_ip") authenticated=$(mask_text "$ip"))"
        fail=1
      fi
    else
      echo "WARN proxychains end-to-end produced no IP"
    fi
  else
    echo "WARN skipping proxychains end-to-end (no authenticated exit IP to compare)"
  fi

  # --- EXPECT_EXIT_IP ---
  if [[ -n "${EXPECT_EXIT_IP:-}" ]]; then
    if [[ -n "$ip" && "$EXPECT_EXIT_IP" == "$ip" ]]; then
      echo "PASS EXPECT_EXIT_IP matches authenticated exit"
    else
      echo "FAIL EXPECT_EXIT_IP mismatch (expected=$(mask_text "$EXPECT_EXIT_IP") got=$(mask_text "${ip:-none}"))"
      fail=1
    fi
  fi

  # --- FLAG (INFO only) ---
  if [[ -e "$FLAG" ]]; then
    echo "INFO FLAG present ($FLAG): WeChat will use the proxy"
  else
    echo "INFO FLAG absent ($FLAG): WeChat will go direct"
  fi

  if [[ $fail -ne 0 ]]; then
    echo "RESULT: FAIL"
    return 1
  fi
  echo "RESULT: PASS"
  return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ $CHECK_MODE -eq 1 ]]; then
  if [[ ! -f "$SECRETS" ]]; then
    echo "FAIL secrets file missing: $SECRETS"
    echo "      run install.sh (without --check) to create a template."
    exit 1
  fi
  if ! load_secrets; then
    echo "FAIL secrets in $SECRETS are missing/unfilled (PHONE_SOCKS_HOST/USER/PASS)."
    exit 1
  fi
  run_check
  exit $?
fi

# ---- install mode ---------------------------------------------------------

# 1. Secrets file must exist and be filled in.
if [[ ! -f "$SECRETS" ]]; then
  mkdir -p "$(dirname "$SECRETS")"
  ( umask 077; cat > "$SECRETS" <<'EOF'
# WeChat domestic exit SOCKS5 proxy secrets.
# Fill in the real values, then rerun:  ./install.sh
# The SOCKS5 server is a phone/PC on the domestic network, reached over Tailscale.
PHONE_SOCKS_HOST=<PHONE_TS_IP>
PHONE_SOCKS_PORT=1080
PHONE_SOCKS_USER=<SOCKS_USER>
PHONE_SOCKS_PASS=<SOCKS_PASS>

# Legacy aliases are also accepted as a fallback:
#   SOCKS_HOST / SOCKS_PORT / SOCKS_USER / SOCKS_PASS
EOF
  )
  chmod 600 "$SECRETS"
  echo "Created a secrets template at: $SECRETS"
  echo "Fill in PHONE_SOCKS_HOST / PHONE_SOCKS_USER / PHONE_SOCKS_PASS, then rerun."
  exit 2
fi

# 2. Enforce secrets mode 600.
smode="$(stat -c '%a' "$SECRETS" 2>/dev/null || echo '000')"
if [[ "$smode" != "600" ]]; then
  echo "WARN: $SECRETS has mode $smode; expected 600. Fixing with chmod 600."
  if ! chmod 600 "$SECRETS" 2>/dev/null; then
    echo "ERROR: cannot chmod $SECRETS to 600 (not the owner?). Refusing to continue." >&2
    exit 1
  fi
fi

# 3. Load and validate secrets.
if ! load_secrets; then
  echo "ERROR: secrets in $SECRETS are missing/unfilled." >&2
  echo "       Edit $SECRETS and rerun." >&2
  exit 2
fi

# 4. Install required packages (idempotent).
need_pkg=0
command -v proxychains4 >/dev/null 2>&1 || need_pkg=1
command -v curl >/dev/null 2>&1 || need_pkg=1
if [[ $need_pkg -eq 1 ]]; then
  if [[ $EUID -eq 0 ]]; then
    apt-get update -y >/dev/null
    apt-get install -y proxychains4 curl
  elif can_sudo; then
    sudo -n apt-get update -y >/dev/null
    sudo -n apt-get install -y proxychains4 curl
  else
    echo "ERROR: proxychains4/curl are missing and no root/sudo is available." >&2
    exit 1
  fi
fi

# 5. Write the proxychains4 config.
write_conf

# 6. Write the launcher.
write_launcher

# 7. Never create the flag file.
echo
echo "--------------------------------------------------------------"
echo "Setup complete."
echo "  secrets : $SECRETS (mode 600)"
echo "  conf    : $CONF"
echo "  launcher: $LAUNCHER"
echo "  wechat  : $WECHAT_BIN"
echo
echo "The proxy is DISABLED by default (flag file absent)."
echo "Create the FLAG only after the account is unbanned / before scanning QR:"
echo "  touch $FLAG"
echo "Remove it to go back to the direct connection:"
echo "  rm -f $FLAG"
