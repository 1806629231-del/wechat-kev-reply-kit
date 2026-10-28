#!/usr/bin/env bash
# 接上微信聊天记录检索（weflow-cli，只读本地微信数据库）。可重复运行。
# 用法：
#   bash scripts/install-weflow.sh            安装 Node22 + weflow-cli + Python 依赖（已装的跳过）
#   bash scripts/install-weflow.sh --key      重登一次微信，用 GDB 抓数据库口令并写入 weflow（需要 sudo，会让微信重新扫码）
#   bash scripts/install-weflow.sh --check    自检：会话数、最近几条消息的条数/时间、关键词检索命中数（不打印聊天内容）
#   KEYWORD=xxx CHAT=<群名或wxid> bash scripts/install-weflow.sh --check
# 环境变量：WEFLOW_HOME(/workspace/weflow-cli) NODE_HOME(/workspace/.tools/node) WX_DATA(/home/box/xwechat_files)
set -u
WEFLOW_HOME="${WEFLOW_HOME:-/workspace/weflow-cli}"
NODE_HOME="${NODE_HOME:-/workspace/.tools/node}"
WX_DATA="${WX_DATA:-/home/box/xwechat_files}"
KEYTOOL_HOME="${KEYTOOL_HOME:-/workspace/wcdb-key-tool}"
NODE_VER="${NODE_VER:-v22.20.0}"
export PATH="$WEFLOW_HOME/.venv/bin:$NODE_HOME/bin:$PATH"
wf(){ (cd "$WEFLOW_HOME" && node cli.cjs "$@" 2>/dev/null); }

install(){
  echo "== 安装 weflow-cli =="
  if ! "$NODE_HOME/bin/node" -v 2>/dev/null | grep -q '^v2[2-9]'; then
    mkdir -p "$NODE_HOME"
    curl -fsSL "https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-linux-x64.tar.xz" | tar -xJ -C "$NODE_HOME" --strip-components=1 || { echo "Node 下载失败"; exit 1; }
  fi
  echo "  node $(node -v)"
  dpkg -s libsqlcipher-dev >/dev/null 2>&1 || sudo apt-get install -y libsqlcipher-dev python3-venv
  [ -d "$WEFLOW_HOME/.git" ] || git clone https://github.com/zhuobichen/weflow-cli "$WEFLOW_HOME" || exit 1
  [ -x "$WEFLOW_HOME/.venv/bin/python" ] || python3 -m venv "$WEFLOW_HOME/.venv"
  "$WEFLOW_HOME/.venv/bin/pip" -q install sqlcipher3 cryptography html2text zstandard
  [ -f "$WEFLOW_HOME/cli.cjs" ] || (cd "$WEFLOW_HOME" && npm install && npm run build) || exit 1
  wf check | tail -5
  echo "  下一步：微信 4.1.12+ 内存里没有现成密钥 → 跑 --key（会重登一次微信）；老版本可直接：cd $WEFLOW_HOME && node cli.cjs init --path $WX_DATA"
}

key(){
  echo "== 抓数据库口令（微信会退出重登一次，请主人准备扫码）=="
  command -v gdb >/dev/null || sudo apt-get install -y gdb
  [ -d "$KEYTOOL_HOME" ] || git clone https://github.com/TANGandXUE/wcdb-key-tool "$KEYTOOL_HOME" || exit 1
  sudo "$WEFLOW_HOME/.venv/bin/python" "$KEYTOOL_HOME/wcdb_key_tool.py" extract || { echo "口令抓取失败 → 退回 OCR"; exit 1; }
  PJ="$(sudo sh -c 'ls /root/.wcdb-key-tool/wechat-passphrase.json 2>/dev/null' || true)"
  [ -f "$HOME/.wcdb-key-tool/wechat-passphrase.json" ] || { [ -n "$PJ" ] && sudo cp "$PJ" "$HOME/.wcdb-key-tool/" 2>/dev/null; sudo chown -R "$(id -u)" "$HOME/.wcdb-key-tool" 2>/dev/null; }
  P="$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.wcdb-key-tool/wechat-passphrase.json')))['passphrase'])")" || exit 1
  wf config set favPassphrase "$P" >/dev/null; unset P
  (cd "$WEFLOW_HOME" && script -qc "node cli.cjs init --refresh --path $WX_DATA" /dev/null | grep -vE '[0-9a-f]{32}' | tail -5)
  chmod 600 "$HOME/.wcdb-key-tool/wechat-passphrase.json"
}

check(){
  echo "== 聊天记录检索自检（只打印数量和时间，不打印内容）=="
  F=0
  [ -f "$WEFLOW_HOME/cli.cjs" ] && echo "  [OK] weflow-cli 已装" || { echo "  [缺] weflow-cli 未装（跑 install-weflow.sh）"; return 1; }
  node -v 2>/dev/null | grep -q '^v2[2-9]' && echo "  [OK] node $(node -v)" || { echo "  [缺] Node 22+"; F=1; }
  wf config show | grep -E '^(NT 密钥|4.x 密钥)' | sed 's/^/  [i] /'
  S="$(wf sessions -n 20 --json)"
  CHAT="${CHAT:-}" KEYWORD="${KEYWORD:-}" WEFLOW_HOME="$WEFLOW_HOME" python3 - "$S" <<'PY' || F=1
import json,os,sys,subprocess,datetime as dt
try: d=json.loads(sys.argv[1]); ss=d.get("sessions",d) if isinstance(d,dict) else d
except Exception: print("  [缺] 读会话失败（多半是没抓到数据库密钥 → --key，或退回 OCR）"); sys.exit(1)
if not ss: print("  [缺] 会话列表为空"); sys.exit(1)
print(f"  [OK] 读到会话 {len(ss)} 个")
chat=os.environ.get("CHAT") or (ss[0].get("username") or ss[0].get("userName") or ss[0].get("talker"))
r=subprocess.run(["node","cli.cjs","messages",chat,"-n","5","--json","--non-interactive"],cwd=os.environ["WEFLOW_HOME"],capture_output=True,text=True,timeout=60)
try: m=json.loads(r.stdout); ms=m.get("messages",m) if isinstance(m,dict) else m
except Exception: print("  [缺] 读最近消息失败"); sys.exit(1)
ts=[x.get("createTime") for x in ms if x.get("createTime")]
f=lambda t: dt.datetime.fromtimestamp(int(t)).strftime("%m-%d %H:%M")
print(f"  [OK] 最近消息 {len(ms)} 条" + (f"，时间 {f(min(ts))} ~ {f(max(ts))}" if ts else ""))
kw=os.environ.get("KEYWORD")
if kw:
    r=subprocess.run(["node","cli.cjs","messages",chat,"-n","500","--json","--non-interactive"],cwd=os.environ["WEFLOW_HOME"],capture_output=True,text=True,timeout=120)
    try: m=json.loads(r.stdout); ms=m.get("messages",m) if isinstance(m,dict) else m
    except Exception: ms=[]
    hit=[x for x in ms if kw in str(x.get("content") or "")+str(x.get("parsedContent") or "")]
    print(f"  [OK] 关键词检索：最近 {len(ms)} 条里命中 {len(hit)} 条")
PY
  [ $F = 0 ] && echo "== 检索可用 ==" || echo "== 检索不可用：mention_watch 会自动退回截屏 OCR =="
  return $F
}
case "${1:-}" in --check) check; exit $?;; --key) key;; *) install;; esac
