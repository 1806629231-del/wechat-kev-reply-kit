#!/usr/bin/env bash
# kev 判断 + 大模型 API 回复 安装包。用法：
#   bash install.sh            安装/渲染到 $WX_HOME（默认 /workspace/wxauto），不会启动任何进程
#   bash install.sh --check    只做自检，不改任何文件、不发任何消息
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
WX_HOME="${WX_HOME:-/workspace/wxauto}"; KEV_HOME="${KEV_HOME:-/workspace/kev}"
ok(){ echo "  [OK] $*"; }; bad(){ echo "  [缺] $*"; FAIL=1; }; FAIL=0
check(){
  echo "== 自检 =="
  [ -f "$WX_HOME/.env" ] && ok ".env 存在" || bad "$WX_HOME/.env 不存在（从 .env.example 复制）"
  if [ -f "$WX_HOME/.env" ]; then
    set -a; . "$WX_HOME/.env"; set +a
    [ -n "${LLM_API_KEY:-}" ] && [[ "$LLM_API_KEY" != \<* ]] && ok "LLM_API_KEY 已填" || bad "LLM_API_KEY 未填"
    for v in BOT_NICK OWNER_NICK WX_GROUPS; do eval x=\${$v:-}; [ -n "$x" ] && [[ "$x" != \<* ]] && ok "$v=$x" || bad "$v 未填"; done
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -H "Authorization: Bearer $LLM_API_KEY" "${LLM_BASE_URL%/}/models")
    [ "$code" = 200 ] && ok "大模型 API 可用 ($LLM_BASE_URL)" || bad "大模型 API 返回 $code"
  fi
  KU="${KEV_URL:-http://127.0.0.1:8009}"
  curl -s -m 5 "$KU/v1/models" >/dev/null && ok "kev 服务在线 $KU" || bad "kev 服务未启动（见 getting-started 第 10 步）"
  if curl -s -m 5 "$KU/v1/models" >/dev/null; then
    KEV_URL="$KU" python3 "$HERE/kev-client/decide.py" choice "新消息: 帮我查下明天天气" "chat or task?" chat task 2>/dev/null | grep -q '"choice"' && ok "kev 判断能出结果" || bad "kev 判断调用失败"
  fi
  [ -f "$WX_HOME/persona.md" ] && ok "人设 persona.md 存在" || bad "persona.md 不存在"
  [ -x "$WX_HOME/.venv/bin/python" ] && ok "wxauto venv 存在" || bad "wxauto venv 不存在"
  python3 -c "import json;c=json.load(open('$WX_HOME/humanize/config.json'));print('  [i] SEND_ENABLED =',c['SEND_ENABLED'],' 群日上限',c['per_group_daily'],' 全天上限',c['total_daily'])" 2>/dev/null || bad "humanize/config.json 读不到"
  [ -f "$WX_HOME/humanize/KILL" ] && echo "  [i] KILL 急停开关：已打开（不会发）" || echo "  [i] KILL 急停开关：未打开"
  if [ "${WEFLOW_READ:-1}" = 1 ]; then bash "$HERE/scripts/install-weflow.sh" --check >/tmp/weflow-check.log 2>&1 && ok "聊天记录检索 weflow 可用" || bad "weflow 检索不可用（看 /tmp/weflow-check.log；不行就 .env 设 WEFLOW_READ=0 退回 OCR）"; else echo "  [i] WEFLOW_READ=0：用截屏 OCR 读消息"; fi
  pgrep -f mention_watch.py >/dev/null && echo "  [i] mention_watch 在跑" || echo "  [i] mention_watch 没在跑"
  pgrep -f watchdog.sh >/dev/null && echo "  [i] 本地保活 watchdog 在跑" || echo "  [i] 本地保活 watchdog 没在跑（第 16 步再开）"
  for b in xdotool xclip; do command -v $b >/dev/null && ok "$b" || bad "缺 $b（apt install $b）"; done
  [ $FAIL = 0 ] && echo "== 全部通过 ==" || echo "== 有缺项，按上面 [缺] 处理 =="; return $FAIL
}
if [ "${1:-}" = "--check" ]; then check; exit $?; fi
echo "== 安装到 $WX_HOME =="
mkdir -p "$WX_HOME/humanize"
[ -f "$WX_HOME/.env" ] || { cp "$HERE/.env.example" "$WX_HOME/.env"; chmod 600 "$WX_HOME/.env"; echo "  已生成 .env，请填好再重跑"; }
set -a; . "$WX_HOME/.env"; set +a
render(){ sed -e "s|{BOT_NICK}|${BOT_NICK}|g" -e "s|{OWNER_NICK}|${OWNER_NICK}|g" "$1" > "$2"; }
for f in mention_watch.py send.py weflow_read.py task_worker.py mention_watch.launch.sh watchdog.sh wx_persist.sh; do
  [ -f "$WX_HOME/$f" ] && cp "$WX_HOME/$f" "$WX_HOME/$f.bak.$(date +%s)"; render "$HERE/wxauto/$f" "$WX_HOME/$f"; done
chmod +x "$WX_HOME/mention_watch.launch.sh" "$WX_HOME/watchdog.sh" "$WX_HOME/wx_persist.sh"
for f in gate.py hsend.py human.py; do render "$HERE/wxauto/humanize/$f" "$WX_HOME/humanize/$f"; done
[ -f "$WX_HOME/humanize/config.json" ] || cp "$HERE/wxauto/humanize/config.json" "$WX_HOME/humanize/"
python3 - "$WX_HOME/humanize/config.json" <<'PY'
import json,sys; p=sys.argv[1]; c=json.load(open(p)); c["SEND_ENABLED"]=False; json.dump(c,open(p,"w"),ensure_ascii=False,indent=2)
PY
[ -f "$WX_HOME/persona.md" ] || render "$HERE/wxauto/persona.example.md" "$WX_HOME/persona.md"
touch "$WX_HOME/tasks.jsonl" "$WX_HOME/mention_log.jsonl"
if [ ! -x "$WX_HOME/.venv/bin/python" ]; then python3 -m venv "$WX_HOME/.venv" && "$WX_HOME/.venv/bin/pip" -q install mss numpy pillow python-dotenv rapidocr-onnxruntime; fi
if [ ! -d "$KEV_HOME" ]; then git clone https://github.com/jaredpalmer/kev "$KEV_HOME"; fi
cp "$HERE/kev-client/decide.py" "$KEV_HOME/decide.py"
echo "== 安装完成。SEND_ENABLED 已强制为 false。下一步：bash install.sh --check，然后先 --once --dry 演练 =="
