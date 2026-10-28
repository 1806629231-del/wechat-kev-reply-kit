---
name: wechat-domestic-exit-proxy
description: >-
  Use when a WeChat account runs on a box/cloud/overseas Linux machine and needs
  a stable domestic (mainland China) exit IP to avoid risk-control bans – e.g.
  after a ban, before scanning the login QR, or when migrating the bot WeChat to
  a new machine. Routes only WeChat through a SOCKS5 proxy on a spare phone or
  PC in China, reached over Tailscale.
---
# Domestic exit IP for a box-hosted WeChat

## Problem
The bot WeChat ran on a box whose public IP is in the US and changed after a reset. WeChat saw the account
hop between foreign IPs and banned it (until evening). Fix: send **only WeChat's** traffic through a
SOCKS5 proxy on a device that sits on a fixed domestic network, so WeChat always sees the same Chinese ISP IP.
Not a guarantee against bans, but removes the most obvious trigger.

## When to use
- Before (re)logging in a WeChat that lives on a server/box outside China, or after it got risk-controlled.
- When moving the bot WeChat to another machine (reuse the same exit device + secrets).

## Prerequisites
- Box: Linux with the WeChat Linux client, sudo, Tailscale joined to the tailnet.
- Exit device (pick one), always on, in **one fixed place** on a domestic network **without** a VPN/side-router tunnel:
  - Option A (used, working): spare Android phone on fixed Wi-Fi + Tailscale + open-source SOCKS5 app
    **hev socks5** (github.com/heiher/socks5, MIT).
  - Option B (backup): Windows PC + Tailscale + **gost** v3 (github.com/go-gost/gost, official releases).
- Accounts: Tailscale tailnet; the phone's app-store/system account password may be needed to install an APK
  (Honor/Huawei asks for the Huawei ID password) – the user provides it via a chmod-600 secrets file, never in chat.

## Steps
1. **Check the exit first** – before installing anything, open myip.ipip.net / ipinfo.io *on the exit device*.
   It must be a domestic ISP (e.g. China Telecom, country CN). If it shows a foreign IP, the device is behind a
   tunnel (VPN app or side router) – stop and pick another device.
2. Install the proxy on the exit device (README.md: phone steps for A, `install-gost-windows.ps1` for B).
   Do keep-alive **in this same step**, not later when it dies:
   - Auth username+password; listen **only on the device's Tailscale IP** (100.x) port 1080.
   - Phone: **Listen IPv6 only = OFF** (if ON, IPv4 :1080 refuses while the UI still shows Stop). Lock Tailscale +
     Socks5 in Recents; App launch = Manage manually (auto-start / associated / background all ON); battery =
     no optimization. Verify from the box that TCP 1080 accepts before continuing.
   - PC: sleep = never; gost autostart; firewall limited to 100.64.0.0/10.
3. Box: put credentials in the secrets file (`PHONE_SOCKS_HOST/PORT/USER/PASS`, chmod 600), run `bash install.sh`.
   It installs proxychains4, writes `/etc/proxychains-wx.conf` (strict_chain, proxy_dns) and a launcher
   `wechat-launch.sh` that uses proxychains **only when the flag file exists**.
4. `bash install.sh --check` – exit IP via the proxy is CN and equals the device's own exit; unauthenticated
   and wrong-password connections are refused; proxychains end-to-end works.
5. Only after the account is unbanned: create the flag file, (re)start WeChat via the launcher, scan the QR with
   the bot's own phone account, then confirm all WeChat connections go via the proxy.

## Verify (manual)
`curl --socks5-hostname USER:PASS@<PHONE_TS_IP>:1080 https://myip.ipip.net` → domestic ISP, same as on the device.
`curl --socks5-hostname <PHONE_TS_IP>:1080 http://ipinfo.io/ip` (no creds) → must fail (curl exit 97).

## Option B caveat (side routers)
A PC whose gateway is a side router that tunnels abroad (here: gateway manually set to the side router to get
around the firewall, while the automatic gateway is the domestic broadband) exits with a **foreign** IP even
through gost – useless. Either run the proxy on a device using the domestic gateway, or add a
"GEOIP-CN / bypass mainland" direct rule on the side router. Windows can't route one app via another gateway
without driver-level tools. **Do not change the gateway of a PC the owner needs for the foreign route.**

## Troubleshooting
- WeChat has no network: proxy device offline (strict_chain intentionally never falls back to the box IP).
  Check `tailscale status`, then the proxy app on the device.
- Phone killed the proxy / UI says Stop but box gets connection refused: open Socks5, turn **Listen IPv6 only OFF**,
  Save, Stop then Start; re-lock in Recents; confirm app-launch = manual (auto/associated/background on) and no
  battery optimization. These keep-alive toggles belong in first-time setup (step 4 / Option A), not only here.
- Exit changed city/ISP: the device moved networks – keep it in one place.
- WeChat face verification on the phone exits instantly: accessibility/screen-capture/overlay apps (and possibly the VPN) are active – the user disables them temporarily.
- After a box reset or "Update Bot's Computer", Tailscale, proxychains4 or this conf can vanish while `/workspace` survives. Run the `wechat-box-stack-heal` pack (`install.sh`, then `install.sh --check`) to rebuild the stack.
- `tailscaled` is alive but its local API is dead (the phone SOCKS path then fails): restart it with `wechat-box-stack-heal` / its `ts-watchdog.sh`; see Related below.
- WeChat helpers look at the wrong desktop after a desktop restart: pin `WX_DISPLAY` via `wechat-box-stack-heal`.

## Security
- Proxy listens only on the Tailscale IP, requires auth; firewall (PC) restricted to 100.64.0.0/10.
- Credentials only in chmod-600 secrets files; pass to curl via `-K -` so they don't show in `ps`.
- If a proxy password ever appears in logs/chat, **rotate it immediately** (happened once; rotated).
- Never create the flag file or log into WeChat without the owner saying the account is unbanned/ready.

## Related
- `wechat-box-stack-heal` — rebuild Tailscale, proxychains and the domestic exit after a box reset; watchdog and display pin.
- `android-remote-control` — phone-side Tailscale/AutoX operation and keep-alive.
