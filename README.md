# kev 判断 + 大模型回复 · 安装包

把「微信里被 @ → kev 判断 → 调大模型 API 回复 → 限速发送」这套流程装到一台已经登好微信的 Linux 电脑上。完整原理见 `FLOW.md`。

## 前提（本包不负责）
先按「微信机器人接入向导」做完：Linux 版微信能登录、走国内出口代理、xdotool/xclip 可用、（推荐）weflow-cli 能读本地消息库。

## 步骤
1. **装文件**：`bash install.sh`。第一次会生成 `/workspace/wxauto/.env`，填好再跑一次。已有同名文件会自动备份成 `.bak.时间戳`。
2. **填配置**（`.env`，权限 600）：API 地址/Key/模型、机器人昵称和别名、主人昵称、允许回复的群。Key 不要贴进聊天。
3. **起 kev 服务**：
   ```bash
   cd /workspace/kev && python3 -m venv .venv && .venv/bin/pip install -e .   # 或 uv sync
   nohup .venv/bin/python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009 > serve.log 2>&1 &
   ```
   首次会下载 kev-0.8b 模型；CPU 能跑，单次判断约几百毫秒。
4. **改人设**：编辑 `/workspace/wxauto/persona.md`。
5. **表情包（可选）**：图片放 `stickers/<分类>/`，在 `stickers/catalog.json` 每张写一条（path、category、desc、intent）；可跑 `.venv/bin/python sticker_pick.py --tag` 让大模型补标签。
6. **自检**：`bash install.sh --check`，全部 [OK] 才往下。
7. **演练（不发）**：`cd /workspace/wxauto && .venv/bin/python mention_watch.py --once --dry`，让朋友在群里 @ 一下，看 `mention_log.jsonl` 里的 `dry_ok` 和 `reply`。
8. **常驻（仍不发）**：`nohup ./mention_watch.launch.sh > watch.out 2>&1 &`，观察一两天回复质量。
9. **开发送**：把 `humanize/config.json` 的 `SEND_ENABLED` 改成 `true`，重启 mention_watch。

## 急停与限额
- 急停：`touch /workspace/wxauto/humanize/KILL`；恢复：删掉这个文件。
- 限额在 `humanize/config.json`，默认：间隔 12 秒起、同聊天 40 秒、单群日 20 条、全天 60 条、23–8 点不发。**只建议往严了调。**

## 文件
- `wxauto/mention_watch.py` 主循环；`send.py` 发送；`weflow_read.py` 读消息；`sticker_pick.py` 选表情；`task_worker.py` 任务队列（可选，语音/手机/唤醒 Grok Bot 部分需另装，缺了会自动跳过或报错记日志）
- `wxauto/humanize/` 发送闸门；`wxauto/persona.example.md` 人设模板
- `kev-client/decide.py` kev 客户端
- `.env.example` 配置模板；`install.sh` 安装 + `--check` 自检
