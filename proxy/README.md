# wechat-domestic-exit-proxy – portable pack

Give a box/cloud-hosted Linux WeChat a stable mainland-China exit IP by routing only WeChat through a SOCKS5
proxy on a domestic device reached over Tailscale. Bot 侧流程见 getting-started.md 第 4 步。

## Contents
- `SKILL.md` – the skill
- `install.sh` – box side: proxychains4 + `/etc/proxychains-wx.conf` + flag-gated `wechat-launch.sh`; `--check`
- `install-gost-windows.ps1` – option B: gost SOCKS5 on a Windows PC (Tailscale-only), `-Check`

## 0. Pick and test the exit device (before installing anything)
On the device itself open https://myip.ipip.net and https://ipinfo.io . It must show a domestic ISP (CN).
Foreign IP ⇒ a VPN app or a side router tunnels it abroad ⇒ choose another device (or add GEOIP-CN direct on the
side router). Keep the device in ONE place on ONE network forever (moving changes the exit IP).

## Option A – spare Android phone (used, working)
1. Tailscale installed, logged into the tailnet, kept alive (lock in Recents; app launch manual; no battery optimization).
2. Install **hev socks5** (open source, MIT): APK from https://github.com/heiher/socks5/releases . Honor/Huawei may ask
   for the Huawei ID password – the owner enters it (or supplies it in a chmod-600 file on the box; type once, never log).
3. Configure: listen address = the phone's **Tailscale IP** (`<PHONE_TS_IP>`), port 1080, auth username/password
   (random, ≥16 chars), start on boot ON, battery optimization OFF, app launch manual (auto/associated/background ON).
4. Confirm the LAN IP and 127.0.0.1 are NOT listening; only the 100.x address.

## 方案 B – Windows 电脑（备用）：优先由 Bot 远程完成
1. Bot 先调 `ListMachines`，看这台 Windows 电脑有没有连到 Grok Bot。
2. **已连上**：Bot 带 machineId 远程执行，主人只在弹出的本地执行审批里点同意：
   - 检测出口 IP：`curl.exe -s myip.ipip.net`，必须是国内运营商；不是就先问主人网关/代理软件怎么设，别硬装。
   - 用管理员权限跑 `powershell -ExecutionPolicy Bypass -File install-gost-windows.ps1 -User <SOCKS_USER>`，不传密码让脚本自动生成。脚本会下载官方 gost、只绑定 Tailscale IP、防火墙只放行 100.64.0.0/10、关休眠、开机自启并检查出口国家。
   - Bot 自己读回 Tailscale IP、SOCKS 用户名和密码，写进 box 上 chmod 600 的密钥文件。不要让主人抄，不在聊天里显示密码。
   - 只有 Tailscale 第一次登录需要主人点一下。
3. **没连上**：Bot 先问一句要不要把电脑连到 Grok Bot。主人不愿意时才手动跑上面的脚本，账号密码由 Bot 用密钥输入框收，绝不发在聊天里。
4. 脚本提示 "exit is not CN"：说明网关走了旁路由/VPN，换设备或在路由上加 GEOIP-CN 直连；主人需要国外线路时不要强行改。

## Box side
```bash
bash install.sh                       # first run writes ~/secrets/wx-socks.env template (chmod 600) and exits
$EDITOR ~/secrets/wx-socks.env        # PHONE_SOCKS_HOST=<PHONE_TS_IP> PHONE_SOCKS_PORT=1080 PHONE_SOCKS_USER=… PHONE_SOCKS_PASS=…
bash install.sh                       # installs proxychains4, writes /etc/proxychains-wx.conf + ~/bin/wechat-launch.sh
bash install.sh --check               # exit IP is CN & matches device; no-auth / wrong-pass refused; proxychains e2e
# only when the account is unbanned / ready to log in:
touch ~/.wx_proxy_enabled && ~/bin/wechat-launch.sh   # then scan QR with the bot account's own phone
```
Options: `--secrets FILE --conf FILE --launcher FILE --flag FILE --wechat-bin PATH`, `EXPECT_EXIT_IP=…` to pin the exit.

## Migrate
Copy the pack + the secrets file (securely, chmod 600) to the new box, join the tailnet, run `install.sh` and
`--check`; the exit device stays the same, so WeChat keeps seeing the same domestic IP.
