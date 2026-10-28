# kev + 大模型 API 微信回复流水线：安装、运维、排查

用于：安装/检查/修理「微信被 @ → kev 判断 → 大模型回复 → 闸门发送」流水线，或有人说机器人不回复、回复怪、发太多。原理见安装包 `FLOW.md`。

## 位置
- 代码 `/workspace/wxauto/`（mention_watch.py、send.py、weflow_read.py、sticker_pick.py、task_worker.py、persona.md、.env、humanize/）
- kev `/workspace/kev/`，服务 `127.0.0.1:8009`，接口 `POST /v1/systemone`、`GET /v1/models`
- 日志 `mention_log.jsonl`（每条带 kev/kev_p/llm/cls/event）、`task_worker_log.jsonl`、`watchdog.log`

## 安装
`bash install.sh` → 填 `.env` → 起 kev（`.venv/bin/python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009`）→ `bash install.sh --check` → `mention_watch.py --once --dry` → 常驻 dry → 主人明确同意后 `SEND_ENABLED=true` 并重启。主人电脑上的操作先 ListMachines，连上就用 machineId 远程做。

## 硬规则
- 群里只回 @；不群发；主动私聊默认不开。
- `SEND_ENABLED` 默认 false；限额只收紧不放松；`humanize/KILL` 存在即全停。
- 风控提示 → touch KILL + 停监听 + 告诉主人，不重试。
- 改 mention_watch/send 前先备份；不要在未经同意时重启正在跑的进程或重置 box。

## 排查顺序（「不回复了」）
1. `tail mention_log.jsonl`：最近 event 是什么。
   - `logged_out` / `logged_out_waiting` → 微信掉线，先查国内出口代理，再让主人扫码（见微信接入向导）。
   - `no_window` → 微信没开或 DISPLAY 不对（`.wx_display`）。
   - `dry_ok` → SEND_ENABLED 还是 false 或带了 --dry。
   - 闸门原因 `kill_switch / quiet_hours / *_daily_cap / global_gap` → 按设计拦下。
   - `llm_error` / `llm_fallback` → API Key、余额、BASE_URL；`curl $LLM_BASE_URL/models`。
   - `weflow_error_fallback_ocr` → weflow 读库失败，已退回 OCR，查 weflow-cli。
   - `mention_not_found` → 聊天窗没加载，会自动重扫。
2. `pgrep -af mention_watch`、`ss -ltnp | grep 8009` 看进程。
3. kev 挂了不影响回复（只少了投票和表情），但要重启服务。
4. 日志里没有新事件 → 数据库文件签名没变，看微信是否真收到消息。

## 调优
- 回复腔调：改 `persona.md`（下次进程启动生效）。
- 误判任务：改 `_REQ_RE` 请求词或大模型 TASK_RULE；kev 投票只做参考。
- 表情太多/太少：调 `maybe_sticker` 里 noul 概率阈值；目录 `stickers/catalog.json`。
