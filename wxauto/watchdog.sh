#!/bin/bash
# 本地保活（不靠云端定时任务）：每 60 秒看一次，挂了就拉起；humanize/KILL 存在就什么都不拉。
#   nohup bash /workspace/wxauto/watchdog.sh >/dev/null 2>&1 &
# 管：kev 服务(:8009)、mention_watch、task_worker（可选，脚本存在才拉）。不碰微信本身、不重登。
D=$(cd "$(dirname "$0")" && pwd); LOG=$D/watchdog.log; KEV_HOME=${KEV_HOME:-/workspace/kev}
log(){ echo "$(date '+%F %T') $*" >>"$LOG"; }
pgrep -f "$D/watchdog.sh" | grep -vq "^$$\$" && [ "$(pgrep -fc "$D/watchdog.sh")" -gt 1 ] && { echo "已有 watchdog 在跑"; exit 0; }
while true; do
  if [ -f "$D/humanize/KILL" ]; then log "KILL 存在，不拉起"
  else
    if ! curl -s -m 3 http://127.0.0.1:8009/v1/models >/dev/null && [ -x "$KEV_HOME/.venv/bin/python" ]; then
      log "拉起 kev"; (cd "$KEV_HOME" && nohup .venv/bin/python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009 >>"$KEV_HOME/serve.log" 2>&1 &); fi
    pgrep -f "mention_watch.py" >/dev/null || { log "拉起 mention_watch"; nohup bash "$D/mention_watch.launch.sh" >>"$D/mention_watch.log" 2>&1 & }
    if [ -f "$D/task_worker.py" ] && ! pgrep -f "task_worker.py" >/dev/null; then
      log "拉起 task_worker"; (cd "$D" && nohup .venv/bin/python task_worker.py >>"$D/task_worker.log" 2>&1 &); fi
  fi
  tail -n 1000 "$LOG" >"$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"; sleep 60
done
