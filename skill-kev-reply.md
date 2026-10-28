# 微信 @ 回复机器人全流程：安装、运维、排查

用于：安装/检查/修理整条链路「国内出口 → 微信只走代理登录 → weflow 读聊天记录 → kev 判断 → 大模型回复 → 闸门发送 → 本地保活」；或有人说机器人不回复、掉线、回复怪、发太多、box 重置了。原理见 `FLOW.md`，首次配置按 `getting-started.md`（16 步）。

## 位置
- 安装包：`install-all.sh`（总编排，`--check` 只读）、`proxy/`、`box-stack/`、`install.sh`、`scripts/install-weflow.sh`
- 代码 `/workspace/wxauto/`（mention_watch.py、send.py、weflow_read.py、task_worker.py、persona.md、.env、humanize/、wx_persist.sh、watchdog.sh）
- 出口密钥 `~/secrets/phone-socks.env`（600）；代理配置 `/etc/proxychains-wx.conf`；启动器 `~/bin/wechat-launch.sh`（要 `~/.wx_proxy_enabled`）；`~/bin/ts-watchdog.sh`
- weflow `/workspace/weflow-cli`（Node 22 在 `/workspace/.tools/node`），口令缓存 `~/.wcdb-key-tool/wechat-passphrase.json`
- kev `/workspace/kev/`，服务 `127.0.0.1:8009`
- 日志 `mention_log.jsonl`、`task_worker_log.jsonl`、`watchdog.log`

## 硬规则
- 只用小号；群里只回 @；不群发；主动私聊默认不开。
- 微信只经代理启动；出口必须国内；代理断了宁可断网。
- `SEND_ENABLED` 默认 false，主人明确说「开」才开；限额只收紧不放松；`humanize/KILL` 存在即全停。
- 风控提示 → touch KILL + 停监听 + 告诉主人，不重试、不反复登录。
- 密钥只用密钥输入框收，不在聊天里出现。
- 主人电脑上的事先 ListMachines，连上就用 machineId 远程做。
- 保活用本地 watchdog，不建频繁云端定时任务；不轻易重置 box。

## 排查顺序（「不回复了」）
1. `bash install-all.sh --check`，先看 FAIL 在哪一层。
2. **出口/网络**：`SOCKS exit` 不是国内或连不上 → 出口设备离线（安卓：Tailscale 和 hev socks5 被系统杀了，先开 Tailscale 再停/启代理，因为代理只绑 100.x 地址）；box 侧 `tailscale status` 卡住就重启 tailscaled（ts-watchdog 会做）。**出口没好之前不要启动/扫码微信。**
3. **登录**：日志 `logged_out` → 出口确认后让主人扫码；box 重置过先 `wx_persist.sh restore`。
4. `tail mention_log.jsonl` 看最近 event：
   - `no_window` → 微信没开或 DISPLAY 不对（`.wx_display` / `~/.config/wechat-box/env`）。
   - `dry_ok` → SEND_ENABLED 还是 false。
   - `kill_switch / quiet_hours / *_daily_cap / global_gap` → 闸门按设计拦下。
   - `llm_error` / `llm_fallback` → API Key、余额、BASE_URL。
   - `weflow_error_fallback_ocr` → 见下面检索排查。
   - `mention_not_found` → 聊天窗没加载，会自动重扫。
5. 进程：`pgrep -af "mention_watch|watchdog|task_worker"`、`ss -ltnp | grep 8009`。watchdog 没跑就 `nohup bash /workspace/wxauto/watchdog.sh &`。
6. kev 挂了不影响写回复（只少了投票），但 send.py 核对窗口要用，watchdog 会拉起。

## 聊天记录检索排查（weflow）
- `KEYWORD=<词> bash scripts/install-weflow.sh --check`：只打印会话数、最近消息条数/时间、命中数。
- 会话读不到 / `未匹配到数据库密钥` / WCDB -1006 → 微信升级或换账号，口令失效 → 征得同意后 `--key`（会重登一次）。
- `node` 版本 <22 → 用 `/workspace/.tools/node/bin`；`sqlcipher3` 导入失败 → `apt install libsqlcipher-dev` 后重装 venv。
- 一时修不好 → `.env` 设 `WEFLOW_READ=0` 退回 OCR（只看当前打开的会话），告诉主人。

## 调优
- 回复腔调：改 `persona.md`（重启进程生效）。
- 误判任务：改 `_REQ_RE` 请求词或 TASK_RULE；kev 投票只做参考。
