#!/usr/bin/env python3
"""weflow_read.py — read Linux WeChat messages via weflow-cli (no OCR).

Used by mention_watch.py as the primary message-reading path (OCR is the
fallback). Read-only; never prints secrets.

Message schema (weflow `messages ... --json`): localId, serverId, localType,
createTime (unix s), isSend (1=from me), senderUsername, senderDisplay,
content, parsedContent.
Groups: username endswith '@chatroom'. Incoming @-mentions look like
'@{BOT_NICK}\u2005...' / '@{BOT_NICK}\u2005...' (U+2005 after the name).
"""
from __future__ import annotations
import json, os, re, subprocess

NODE = "/workspace/.tools/node/bin/node"
CLI = "/workspace/weflow-cli/cli.cjs"
CWD = "/workspace/weflow-cli"

ME_NAMES = ("{BOT_NICK}", "{BOT_NICK}", "{BOT_NICK}")
# @<name> followed by U+2005 / whitespace / end / punctuation
_AT_RE = re.compile(r"@(?:{BOT_NICK}|{BOT_NICK}|{BOT_NICK})(?:\u2005|\u2004|\s|$|[，,。.!！?？:：])")

# Official / service accounts to skip (by username and by display)
OFFICIAL_USERS = {
    "weixin", "filehelper", "fmessage", "medianote", "floatbottle",
    "newsapp", "notifymessage", "notification_messages", "weixinreminder",
    "tmessage", "qqmail", "qmessage", "weibo", "mphelper", "brandsessionholder",
    "wxitil", "weixinteam",
}
OFFICIAL_PREFIX = ("gh_",)  # official accounts
OFFICIAL_DISPLAY = (
    "腾讯新闻", "微信团队", "文件传输", "服务通知", "微信支付", "微信运动",
    "订阅号", "qq邮箱", "tencent news", "weixin team", "file transfer",
    "service account", "service acc", "公众号", "服务号",
)


def _env():
    e = os.environ.copy()
    e["PATH"] = f"/workspace/weflow-cli/.venv/bin:/workspace/.tools/node/bin:" + e.get("PATH", "")
    return e


def _run(args, timeout=60):
    r = subprocess.run([NODE, CLI, *args], capture_output=True, text=True,
                       env=_env(), cwd=CWD, timeout=timeout)
    out = (r.stdout or "").strip()
    # strip node ExperimentalWarning lines if any leaked to stdout
    if not out:
        raise RuntimeError("weflow empty stdout: " + (r.stderr or "")[-200:])
    # find first JSON object/array
    i = min((out.find(c) for c in "{[" if out.find(c) >= 0), default=-1)
    if i < 0:
        raise RuntimeError("weflow non-json: " + out[:200])
    return json.loads(out[i:])


def sessions(limit=20):
    d = _run(["sessions", "-n", str(limit), "--json"])
    if isinstance(d, dict):
        return d.get("sessions") or []
    return d if isinstance(d, list) else []


def messages(talker_username, n=20):
    d = _run(["messages", talker_username, "-n", str(n), "--json", "--non-interactive"])
    if isinstance(d, dict):
        return d.get("messages") or []
    return d if isinstance(d, list) else []


def is_group(username: str) -> bool:
    return str(username).endswith("@chatroom")


def is_official(username: str, display: str = "") -> bool:
    u = str(username or "").lower()
    if u in OFFICIAL_USERS:
        return True
    if any(u.startswith(p) for p in OFFICIAL_PREFIX):
        return True
    dn = str(display or "").lower()
    if any(o.lower() in dn for o in OFFICIAL_DISPLAY):
        return True
    return False


def is_at_me(text: str) -> bool:
    return bool(_AT_RE.search(text or ""))


def msg_id(chat_user: str, m: dict) -> str:
    sid = str(m.get("serverId") or "")
    if sid and sid != "0":
        return f"{chat_user}:{sid}"
    return f"{chat_user}:L{m.get('localId')}:{m.get('createTime')}"


def normalize(m: dict, chat_user: str, chat_display: str) -> dict:
    text = m.get("parsedContent") or m.get("content") or ""
    is_send = int(m.get("isSend") or 0) == 1
    return {
        "id": msg_id(chat_user, m),
        "chat_user": chat_user,
        "chat": chat_display,
        "is_group": is_group(chat_user),
        "sender": (m.get("senderDisplay") or m.get("senderUsername") or "").strip(),
        "sender_user": m.get("senderUsername") or "",
        "text": text,
        "is_send": is_send,
        "is_at_me": (not is_send) and is_at_me(text),
        "create_time": int(m.get("createTime") or 0),
        "local_type": m.get("localType"),
    }


def strip_mention_prefix(text: str) -> str:
    """Remove a leading '@name\u2005' so the LLM sees just the request text."""
    return _AT_RE.sub("", text or "", count=1).strip() if _AT_RE.match(text or "") else (text or "")
