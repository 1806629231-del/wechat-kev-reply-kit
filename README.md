# 微信 @ 回复机器人 · 全流程安装包（Grok Bot box 版）

从零到能用的完整链路：国内出口代理 → box 上 Linux 微信（只走代理）→ 登录持久化 → weflow 读本地聊天记录 → kev 本地小模型判断闲聊/任务 → 大模型 API 按人设写回复 → 防封闸门 → 模拟输入发送 → 本地保活。
**先读《封号风险说明.md》**。只用小号，任何方法都不保证不封号。分步引导见 `getting-started.md`（16 步），原理见 `FLOW.md`。

## 目录
| 路径 | 作用 |
|---|---|
| `install-all.sh` | 总编排：box 网络栈 → 代理启动器 → 回复层 → weflow 检索 → 总自检（`--check` 只读） |
| `proxy/` | 国内出口：Windows `install-gost-windows.ps1`（Bot 能远程就远程跑）、安卓 hev socks5 步骤、box 侧 proxychains + `wechat-launch.sh` |
| `box-stack/` | box 重置后修复：Tailscale、`/etc/proxychains-wx.conf`（strict_chain）、ts-watchdog、`WX_DISPLAY` |
| `install.sh` | 回复层安装到 `/workspace/wxauto`，`--check` 自检（含 weflow） |
| `scripts/install-weflow.sh` | 聊天记录检索：装 weflow-cli；`--key` 抓库口令；`--check` 只打印数量/时间 |
| `wxauto/` | `mention_watch.py`（监听+回复）、`weflow_read.py`、`send.py`、`humanize/`（闸门）、`task_worker.py`（可选）、`wx_persist.sh`、`watchdog.sh`、`persona.example.md` |
| `kev-client/decide.py` | kev 判断客户端（只做闲聊/任务投票和发送前核对窗口名） |

## 快速安装
```bash
git clone https://github.com/1806629231-del/wechat-kev-reply-kit && cd wechat-kev-reply-kit
bash install-all.sh            # 首次生成 ~/secrets/phone-socks.env（chmod 600），填好国内出口后重跑
# 装微信：官网 Linux .deb → sudo apt-get install -y /workspace/wechat.deb
touch ~/.wx_proxy_enabled && ~/bin/wechat-launch.sh     # 只走代理启动，扫码登录
# 登录后关微信：bash /workspace/wxauto/wx_persist.sh link，再用启动器打开
bash scripts/install-weflow.sh --key                    # 微信 4.1.12+：重登一次抓口令（需同意）
KEYWORD=你好 bash scripts/install-weflow.sh --check
# 填 /workspace/wxauto/.env（LLM_*、BOT_NICK、OWNER_NICK、WX_GROUPS），重跑 bash install.sh
bash install-all.sh --check
```

## 聊天记录检索（weflow-cli）
- 来源：开源 [zhuobichen/weflow-cli](https://github.com/zhuobichen/weflow-cli)，克隆到 `/workspace/weflow-cli`，Node 22（装在 `/workspace/.tools/node`）+ Python venv（sqlcipher3、cryptography、html2text、zstandard）+ `libsqlcipher-dev`，`npm install && npm run build`。
- 数据目录 `/home/box/xwechat_files`（`WX_DATA` 可改）。只读，不发消息。
- 密钥：老版本微信 `node cli.cjs init --path $WX_DATA` 直接从进程内存取。**微信 4.1.12+ 只在内存留 32 字节口令**，要用 [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool) 在 GDB 断点下**重登一次**抓口令（`--key`），写进 weflow 配置（本机加密）后 `init --refresh` 派生各库密钥。口令缓存 `~/.wcdb-key-tool/wechat-passphrase.json`（chmod 600）。
- `mention_watch.py` 先用 `weflow_read.py` 读新消息；失败记 `weflow_error_fallback_ocr` 并退回截屏 OCR。`.env` 里 `WEFLOW_READ=0` 可强制 OCR。
- 换账号或微信大版本升级后要重跑 `--key`。

## 急停与限额
- 急停 `touch /workspace/wxauto/humanize/KILL`，恢复 `rm` 它。安装后 `SEND_ENABLED` 强制 false。
- 默认：@ 后 2–6 秒、间隔 ≥12 秒、同群 40 秒、单群日 20 条、全天 60 条、23–8 点静默、≤200 字 ≤2 句。只收紧不放松。

## 保活（本地，不用云端定时任务）
`nohup bash /workspace/wxauto/watchdog.sh >/dev/null 2>&1 &`：每 60 秒拉起 kev / mention_watch / task_worker；KILL 存在就不拉。box 网络由 `~/bin/ts-watchdog.sh` 管。

## box 重置后恢复
```bash
bash install-all.sh
bash /workspace/wxauto/wx_persist.sh restore
~/bin/wechat-launch.sh && bash install-all.sh --check
```

## 已知限制
- 发送靠 xdotool 模拟输入，存在被识别为外挂的风险。
- weflow 在微信 4.1.12+ 上需重登一次抓口令；抓不到就只能 OCR（只看当前打开的会话）。
- `task_worker.py` 的语音、手机控制等为可选，依赖本包外的模块。
