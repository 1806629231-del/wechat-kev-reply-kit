# wechat-box-stack-heal – portable pack

**中文摘要（给运维）**：这个包用于 box 被重置或“Update Bot's Computer”之后的一键修复。`/workspace` 通常还在，
但 Tailscale、proxychains4、`/etc/proxychains-wx.conf` 可能已被清空，微信因此失去国内 SOCKS 出口并掉到登录页。
把 `.env.example` 复制成私密 secrets 文件并填好真实值，运行 `bash install.sh`，再运行 `bash install.sh --check`
即可。安装脚本会补装依赖、拉起 `tailscaled`、重建代理配置、安装保守的 Tailscale watchdog、
固定 `WX_DISPLAY`，并执行一个只会补齐缺失项的瘦身版 workspace 自检。它不会自动创建
`~/.wx_proxy_enabled`，也不会打印任何密码。

## Contents
- `SKILL.md` – when to use this, operating rules, troubleshooting, security.
- `install.sh` – idempotent heal for Debian/Ubuntu; `--check` is read-only.
- `scripts/ts-watchdog.sh` – conservative `tailscaled` watchdog (10-minute cooldown).
- `scripts/bootstrap-wx.sh` – slim workspace heal; only starts missing services.
- `.env.example` – template for the private secrets/env file.

## What gets healed
1. `proxychains4` + `curl`, and Tailscale CLI/daemon when missing.
2. `tailscaled` started with a persistent state under `$HOME/tailscale` and the box socket symlink.
3. `/etc/proxychains-wx.conf` rebuilt with `strict_chain` and `proxy_dns`.
4. `~/bin/ts-watchdog.sh` installed; restarts only when the phone is gone and there is no tailnet
   interface, or when the local Tailscale API is dead.
5. `~/.config/wechat-box/env` with `WX_DISPLAY` (default `:2`).
6. `~/bin/bootstrap-wx.sh` installed and run. It adds a PulseAudio virtual mic when the tools exist,
   rebuilds the `wxauto` venv when that directory exists, starts `kev` on `:8009` when present, and
   starts `mention_watch` / `task_worker` only when their scripts exist. It never kills healthy processes.

## Migrate to another box
1. Copy the directory, or make and move an archive:
   ```bash
   tar -czf wechat-box-stack-heal.tar.gz -C /workspace/skill-packs wechat-box-stack-heal
   # on the new box:
   tar -xzf wechat-box-stack-heal.tar.gz -C ~/skill-packs
   ```
2. Create the private secrets file from the template and fill it in:
   ```bash
   cd ~/skill-packs/wechat-box-stack-heal
   install -m 600 -D .env.example "$HOME/secrets/phone-socks.env"
   $EDITOR "$HOME/secrets/phone-socks.env"
   ```
   Keep `PHONE_SOCKS_HOST` / `PHONE_TS_IP` as the exit device's Tailscale IP, and use the same SOCKS
   user/password as the exit device. Never commit or share the filled file.
3. Run the heal:
   ```bash
   bash install.sh
   ```
   Run it as the box/bot user so `$HOME` points at the bot home; the script uses `sudo -n` for the
   privileged steps. Avoid `sudo bash install.sh` unless you really want root's `$HOME`.
   If the secrets file is missing, the script writes a template and exits 2. If it exists but still has
   placeholders, the script exits 2 without changing it.
4. Verify read-only:
   ```bash
   bash install.sh --check
   ```
   Expect `RESULT: PASS`. WARN about phone AutoX is acceptable when the phone/app is temporarily offline;
   a SOCKS exit FAIL means fix the exit device or credentials before starting WeChat.

## Boot-time auto-heal
The installer does not modify system startup on its own. For lasting stability, start the installed
watchdog after boot, for example with a cron entry:
```cron
@reboot sleep 30 && /home/<user>/bin/ts-watchdog.sh >>/tmp/ts-watchdog.boot.log 2>&1
```
and run the bootstrap from a login hook or `@reboot` if the workspace should come back automatically:
```bash
~/bin/bootstrap-wx.sh
```
The box must still be logged in to Tailscale. If the state directory was wiped, run
`sudo tailscale --socket=/run/tailscale/tailscaled.sock up` once; the pack never re-authenticates.

## Enable the domestic exit
The installer deliberately does **not** create `~/.wx_proxy_enabled`. Only after the account owner says
the account is ready:
```bash
touch ~/.wx_proxy_enabled
~/bin/wechat-launch.sh   # from wechat-domestic-exit-proxy
```
Remove the flag to return WeChat to a direct connection.

## Security
- Secrets live only in a chmod-600 file and are parsed, never sourced or printed.
- Authenticated curl uses `-K -` (config on stdin), so credentials do not show in `ps`.
- `/etc/proxychains-wx.conf` is mode 640 and contains the proxy password; regenerate rather than editing.
- Do not put real IPs, account names, IMEI, tokens or passwords in this pack or in chat.
