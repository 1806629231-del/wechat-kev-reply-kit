# kev 判断 + 大模型 API 回复：整套流程说明

## 一句话
微信来了新消息 → 只挑「群里 @ 我」和私聊 → 拿最近聊天当上下文 → 调大模型 API（DeepSeek，OpenAI 兼容接口）按人设写回复，同时让本地 kev 小模型投票「闲聊还是任务」 → 规则合并后定成「回一句」或「排进任务队列」 → 回复过发送闸门（限速、限额、急停） → xdotool 粘贴发出。前面还有一段：国内出口代理 → 微信只走代理登录 → weflow 读本地库。

## 各部件
| 部件 | 文件 | 作用 |
|---|---|---|
| 监听主循环 | `mention_watch.py` | 盯微信本地数据库文件变化（只看 mtime，不解密），有变化才读新消息；120 秒心跳兜底 |
| 读消息 | `weflow_read.py` | 用 weflow-cli 读本地消息库（首选，`scripts/install-weflow.sh`）；失败退回截屏 + RapidOCR |
| 人设 | `persona.md` | 系统提示词正文：口语、短句、禁忌词 |
| 大模型回复 | `mention_watch.llm()` | POST `{LLM_BASE_URL}/chat/completions`，要求输出 JSON：`type`(chat/task)、`reply`、`task`、`deliverable` |
| kev 判断 | kev 服务 `127.0.0.1:8009`，客户端 `decide.py` | `POST /v1/systemone`。两类问题：① `choice` chat/task 投票（只记日志，**kev 单独不会把消息判成任务**）② 发送前 `noul` 核对聊天窗名字对不对 |
| 合并规则 | `llm()` 内 | 纯图片/表情/链接 → 一律闲聊；含「帮我/查/整理/写/生成…」请求词，或大模型判 task 且有明确交付物 → 任务；问「好了吗/进度」→ 直接回任务状态；回复里出现「稍等/在做」但其实没任务 → 改成「没有在跑的任务」防空头承诺 |
| 任务队列 | `tasks.jsonl` + `task_worker.py` | 任务先回「行，我去弄，好了喊你」，worker 再慢慢做（DeepSeek 直出；可选：语音、手机、唤醒 Grok Bot） |
| 发送闸门 | `humanize/gate.py` + `config.json` | 所有发送先 `check()`：KILL 文件、23–8 点静默、单条≤200字且不分段、全局间隔 12–18 秒、同一聊天 40 秒、单群每天 20 条、全天 60 条；@ 后随机等 2–6 秒；只留 1–2 句 |
| 发送 | `send.py` | 确认聊天窗名字对得上 → 剪贴板粘贴 → 回车；dry 模式做到粘贴前为止 |
| 日志 | `mention_log.jsonl` | 每条记 kev 投票、大模型判定、最终分类、耗时、发送结果 |

## 流程图（从登录到回复）
```mermaid
flowchart TD
  P0[国内出口设备<br/>Windows gost / 安卓 hev socks5] -- Tailscale 100.x:1080 --> P1[box: proxychains strict_chain]
  P1 --> W0[wechat-launch.sh 只走代理启动微信]
  W0 --> W1{已登录?}
  W1 -- 否 --> W2[主人用小号扫码 → wx_persist.sh link]
  W2 --> W1
  W1 -- 是 --> A[微信本地库文件变化 / 120s 心跳]
  A --> C[weflow 读新消息<br/>失败退回截屏OCR]
  C --> D{群消息且没@我?}
  D -- 是 --> X[忽略，只记已读]
  D -- 否 --> E[取最近 12~18 行上下文]
  E --> F{问进度?且有任务}
  F -- 是 --> F1[直接回任务状态]
  F -- 否 --> G[大模型 API：人设+上下文 → JSON]
  G --> H[kev choice: chat/task 投票，记日志]
  H --> I{合并规则}
  I -- 任务 --> T[写 tasks.jsonl，回“行，我去弄”] --> TW[task_worker 慢慢做再回（可选）]
  I -- 闲聊 --> J[去推理痕迹/签名/空头承诺]
  F1 --> K
  J --> K{发送闸门 gate.check<br/>KILL/静默/间隔/日上限}
  K -- 拦下 --> K1[记原因，不发]
  K -- 放行 --> L{SEND_ENABLED?}
  L -- false --> L1[dry：只记日志不发]
  L -- true --> M[send.py：kev 核对窗口名 → 粘贴发送 → gate.record]
  G -. API 失败 3 次 .-> FB[固定兜底回复] --> K
  WD[watchdog.sh 每60s 拉起 kev/mention_watch/task_worker] -.-> A
  TSW[ts-watchdog 守 Tailscale] -.-> P1
```

## 防封要点（不能改松）
- 群里只回 @，不群发，不主动拉群发消息。
- `SEND_ENABLED=false` 是默认，先 dry 演练看日志，再打开。
- `touch humanize/KILL` 立刻全部停发；删掉才恢复。
- 遇到任何微信风控提示：先 touch KILL，再停 mention_watch，人工处理。
- 微信只能经 `~/bin/wechat-launch.sh` 走国内出口启动；代理断了就断网，不回落海外 IP。
