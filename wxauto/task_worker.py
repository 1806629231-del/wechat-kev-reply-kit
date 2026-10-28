#!/usr/bin/env python3
"""task_worker.py — local WeChat task worker (long-running).

Picks tasks.jsonl entries the moment they appear (polls every 2s):
  pending      -> DeepSeek routes it: "direct" (answerable from knowledge+chat context:
                  lists/options/short writing/explanations/translations) -> generate,
                  send via send.py (real @requester in groups, plain in DMs), status done.
                  "agent" (needs web/X/files/real tools) -> status needs_agent; the 5-min
                  「微信任务队列处理」routine (Grok Bot) does it.
  unfinished   -> progress update once >=3 min old, then at most every 3.5 min.
  No deadlines: long tasks just keep going with progress updates.
  Failures: each step (DeepSeek, send) retried once; then status failed + failure note via
  fail_notify.py (@requester + @{OWNER_NICK}, or {OWNER_NICK} private chat if he isn't in that chat).
tasks.jsonl writes are serialized by /tmp/wx_tasks.lock; screen sends by send.py's lock.
Usage: .venv/bin/python task_worker.py [--dry] [--once] [--id TASK_ID] [--file PATH]
  --dry: never sends, never writes tasks.jsonl (prints the decisions).
"""
from __future__ import annotations
import argparse, fcntl, json, os, re, sys, time, urllib.request
D = "/workspace/wxauto"; sys.path.insert(0, D)
os.environ.setdefault("DISPLAY", ":2")
os.environ.setdefault("PULSE_SERVER", "unix:/tmp/pulse-IuOdK5lyd4Pb/native")
try:
    from dotenv import load_dotenv; load_dotenv(f"{D}/.env")
except Exception:
    pass
LOCK = "/tmp/wx_tasks.lock"
LOG = f"{D}/task_worker_log.jsonl"
OPEN = ("pending", "running", "in_progress", "needs_agent")
PROGRESS_FIRST, PROGRESS_EVERY = 180, 210
FORCE_FAIL = os.environ.get("WORKER_FORCE_FAIL", "")   # test hook: "llm" or "send"
import fail_notify as FN

def now(): return time.strftime("%Y-%m-%dT%H:%M:%S%z")
def ts_of(s):
    try: return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception: return time.time()
def jlog(o):
    o = {"ts": now(), **o}
    with open(LOG, "a") as f: f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(json.dumps(o, ensure_ascii=False)[:600], flush=True)

class Store:
    def __init__(self, path, dry): self.path, self.dry = path, dry
    def load(self):
        if not os.path.exists(self.path): return []
        out = []
        for l in open(self.path):
            if l.strip():
                try: out.append(json.loads(l))
                except Exception: pass
        return out
    def update(self, tid, fn):
        """Reload under lock, apply fn(row) to row id==tid, atomically rewrite."""
        if self.dry: return
        with open(LOCK, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            rows = self.load()
            for r in rows:
                if r.get("id") == tid: fn(r)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.path)

def deepseek(system, user, max_tokens=700, json_mode=False):
    key = os.getenv("LLM_API_KEY"); base = (os.getenv("LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.getenv("LLM_MODEL") or "deepseek-chat"
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.7}
    if json_mode: body["response_format"] = {"type": "json_object"}
    last = None
    for a in range(2):                              # one automatic retry
        try:
            if FORCE_FAIL == "llm": raise RuntimeError("forced DeepSeek failure (test)")
            rq = urllib.request.Request(base + "/chat/completions", json.dumps(body).encode(),
                                        {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(rq, timeout=60) as r:
                return (json.load(r)["choices"][0]["message"].get("content") or "").strip()
        except Exception as e:
            last = e; time.sleep(1 + a)
    raise last

ROUTE_SYS = ("你是微信{BOT_NICK}的任务调度器。判断这个任务能否只靠你自己的知识和给出的聊天上下文直接完成，"
  "不需要联网搜索、实时信息、刷X/推特、打开链接、读写文件、操作电脑或账号。"
  "能直接完成的（列清单/给选项/短文案/解释/翻译/总结给出的聊天内容/出主意）route=direct，并直接写出要发到微信的最终答案："
  "中文口语、像真人聊天、简洁（一般不超过250字，清单可用1. 2. 3.），不要开场白复述任务，不要署名，不要输出思考过程。"
  "需要真实工具或最新信息的 route=agent，answer 留空。拿不准就 agent。"
  "只输出JSON：{\"route\":\"direct\"或\"agent\",\"answer\":\"...\",\"why\":\"一句话理由\"}")

def route_and_answer(t):
    ctx = "\n".join(t.get("context") or [])[-2500:]
    who = f"群「{t.get('chat')}」里的 {t.get('sender')}" if t.get("is_group") else f"私聊对象 {t.get('chat')}"
    u = f"【最近聊天记录】\n{ctx}\n【记录结束】\n\n{who} 的原话：{t.get('text','')}\n任务：{t.get('task','')}"
    r = deepseek(ROUTE_SYS, u, json_mode=True)
    try: o = json.loads(r[r.find("{"):r.rfind("}") + 1])
    except Exception: return "agent", "", "unparsable"
    route = "direct" if str(o.get("route")).lower() == "direct" and str(o.get("answer", "")).strip() else "agent"
    ans = re.sub(r"\s*[—-]{1,2}\s*{BOT_NICK}[^\n]*$", "", str(o.get("answer", "")).strip()).strip()
    return route, ans, str(o.get("why", ""))[:80]

def send_to(t, text, dry):
    grp = bool(t.get("is_group")) and t.get("sender")
    msg = f"@{t['sender']} {text}" if grp else text
    last = ""
    for attempt in range(2):                        # one automatic retry
        if FORCE_FAIL == "send": last = "forced send failure (test)"; continue
        if dry: return True, f"DRY would send to {t.get('chat')}: {msg[:160]}"
        try:
            import send as SND
            ok, lg = SND.send(t["chat"], msg, dry=False, mention=(t["sender"] if grp else None))
            if not ok and "SEND keypress" in str(lg): ok = True
            if ok: return True, str(lg)[-200:]
            last = str(lg)[-200:]
        except Exception as e:
            last = str(e)[-200:]
        time.sleep(2)
    return False, last

def fail(st, t, what, why, alt, dry):
    res = FN.notify(t, what, why, alt, dry=dry)
    def f(r):
        r["status"] = "failed"
        r.setdefault("updates", []).append({"ts": now(), "note": f"failed: {what} ({why})", "notified": res})
    st.update(t["id"], f)
    jlog({"event": "failed", "id": t["id"], "what": what, "why": why, "notify": res})

VOICE_RE = re.compile(r"语音|说一[个句段遍下]|念一?[个句段遍下]?|读给我听|用声音|讲一[个句段]|绕口令")
VOICE_SYS = ("你是{BOT_NICK}，要用微信语音回复。根据对方的要求写出要念出来的中文口语原文：自然、像真人说话，"
             "不超过60字，不要表情符号、不要@、不要链接、不要署名、不要解释，只输出要念的文字。")

def handle_voice(st, t, ans, dry, t0):
    """Voice request -> TTS pipeline (voice_tts.say); falls back to text on failure."""
    tid = t["id"]
    try:
        ctx = "\n".join(t.get("context") or [])[-1500:]
        spoken = deepseek(VOICE_SYS, f"【最近聊天】\n{ctx}\n对方原话：{t.get('text','')}\n任务：{t.get('task','')}", max_tokens=200).strip()
        spoken = re.sub(r"[@#]\S+", "", spoken).strip().strip('"“”') or ans[:60]
    except Exception:
        spoken = ans[:60]
    if not spoken:
        fail(st, t, "语音内容生成出错", "empty", "稍后再@我一次～", dry); return
    ok, out = False, ""
    for attempt in range(2):
        if dry: ok, out = True, f"DRY voice to {t.get('chat')}: {spoken}"; break
        try:
            import voice_tts as VT
            ok, lg, info = VT.say(t["chat"], spoken, user=t.get("chat_user"))
            out = json.dumps(info, ensure_ascii=False)
            if ok: break
        except Exception as e:
            out = str(e)[-200:]
        time.sleep(2)
    if not ok:
        tok, tout = send_to(t, spoken, dry)  # text fallback
        st.update(tid, lambda r: r.setdefault("updates", []).append({"ts": now(), "text": spoken, "sent": tok, "note": f"voice failed ({out[-60:]}), text fallback"}))
        if not tok: fail(st, t, "语音发不出去", out[-60:], "可以私聊我再要一次～", dry); return
        st.update(tid, lambda r: (r.__setitem__("status", "done"), r.__setitem__("result", spoken)))
        jlog({"event": "voice_fallback_text", "id": tid, "err": out[-200:]}); return
    def fin(r):
        r.setdefault("updates", []).append({"ts": now(), "text": spoken, "sent": True, "note": "voice via voice_tts.say"})
        r["status"] = "done"; r["result"] = spoken
    st.update(tid, fin)
    jlog({"event": "voice_done", "id": tid, "info": out, "total_s": round(time.time() - t0, 1)})

PHONE_RE = re.compile(r"(我的)?手机|phone", re.I)
OWNERS = ("{OWNER_NICK}",)
def handle_phone(st, t, dry, t0):
    """{OWNER_NICK} asks for a phone operation -> phone_agent (DeepSeek + AutoX v3 over Tailscale)."""
    tid = t["id"]
    if dry: jlog({"event": "phone_dry", "id": tid}); return
    try:
        sys.path.insert(0, "/workspace/phone-agent"); import phone_agent as PA
        r = PA.run(t.get("task") or t.get("text") or "", verbose=False)
        msg = ("📱 " if r.get("ok") else "📱 没做完：") + r.get("summary", "") + f"（{r.get('secs')}秒）"
    except Exception as e:
        r, msg = {"ok": False}, f"📱 手机操作失败：{str(e)[:80]}"
    ok, out = send_to(t, msg, dry)
    def fin(rw):
        rw.setdefault("updates", []).append({"ts": now(), "text": msg, "sent": ok, "note": "phone_agent"})
        rw["status"] = "done" if ok else "failed"; rw["result"] = msg
    st.update(tid, fin)
    jlog({"event": "phone_done", "id": tid, "ok": r.get("ok"), "total_s": round(time.time() - t0, 1)})

def handle_pending(st, t, dry):
    tid = t["id"]
    st.update(tid, lambda r: (r.__setitem__("status", "running"),
                              r.setdefault("updates", []).append({"ts": now(), "note": "worker picked up"})))
    t0 = time.time()
    is_owner = t.get("sender") in OWNERS or (not t.get("is_group") and t.get("chat") in OWNERS)
    if is_owner and PHONE_RE.search((t.get("text") or "") + " " + (t.get("task") or "")):
        handle_phone(st, t, dry, t0); return
    try:
        route, ans, why = route_and_answer(t)
    except Exception as e:
        fail(st, t, "AI 生成回复出错", str(e)[:60], "稍后再@我一次，或者让{OWNER_NICK}手动处理一下～", dry); return
    jlog({"event": "routed", "id": tid, "route": route, "why": why, "llm_s": round(time.time() - t0, 1),
          "task": t.get("task", "")[:80], **({"answer": ans} if dry else {})})
    if VOICE_RE.search((t.get("text") or "") + " " + (t.get("task") or "")):
        handle_voice(st, t, ans if route == "direct" else "", dry, t0); return
    if route == "agent":
        st.update(tid, lambda r: (r.__setitem__("status", "needs_agent"),
                                  r["updates"].append({"ts": now(), "note": f"needs tools -> routine ({why})"})))
        if not dry: wake_grokbot(tid)
        return
    try:
        ok, out = send_to(t, ans, dry)
    except Exception as e:
        ok, out = False, str(e)[-200:]
    if not ok:
        st.update(tid, lambda r: r.setdefault("updates", []).append({"ts": now(), "text": ans, "sent": False, "note": "send failed x2"}))
        fail(st, t, "结果发不出去", out[-60:], "我把结果存好了，可以私聊我再要一次～", dry); return
    def fin(r):
        r["updates"].append({"ts": now(), "text": ans, "sent": True, "note": "worker direct answer"})
        r["status"] = "done"; r["result"] = ans
    st.update(tid, fin)
    jlog({"event": "direct_done" if ok else "direct_send_failed", "id": tid, "out": out,
          "total_s": round(time.time() - t0, 1)})

def wake_grokbot(tid):
    """POST to Grok Bot webhook routine so it handles needs_agent tasks (only cost when needed)."""
    try:
        import urllib.request
        cfg = {}
        for ln in open("/workspace/wxauto/.grokbot_webhook", encoding="utf-8"):
            if "=" in ln: k, v = ln.strip().split("=", 1); cfg[k] = v.strip("'\"")
        body = json.dumps({"kind": "task", "task_id": tid}).encode()
        req = urllib.request.Request(cfg["GROKBOT_WEBHOOK_URL"], data=body, method="POST",
              headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["GROKBOT_WEBHOOK_KEY"]})
        with urllib.request.urlopen(req, timeout=20) as r: jlog({"event": "wake_grokbot", "id": tid, "code": r.status})
    except Exception as e:
        jlog({"event": "wake_grokbot_failed", "id": tid, "err": str(e)[:120]})

def last_progress(t):
    ups = [ts_of(u.get("ts")) for u in (t.get("updates") or []) if isinstance(u, dict) and u.get("progress")]
    return max(ups) if ups else 0

def housekeeping(st, t, dry):
    tid = t["id"]; age = time.time() - ts_of(t.get("ts"))
    if t.get("status") in ("needs_agent", "running", "in_progress") and age >= PROGRESS_FIRST:
        lp = last_progress(t)
        # also respect routine's own report.py updates as recent progress
        lr = max([ts_of(u.get("ts")) for u in (t.get("updates") or []) if isinstance(u, dict) and u.get("sent")] or [0])
        if time.time() - max(lp, lr) >= PROGRESS_EVERY:
            mins = int(age // 60)
            txt = f"「{t.get('task','')[:20]}」还在做，已经{mins}分钟了，弄好第一时间发你～"
            try: ok, out = send_to(t, txt, dry)
            except Exception as e: ok, out = False, str(e)[-160:]
            st.update(tid, lambda r: r.setdefault("updates", []).append(
                {"ts": now(), "text": txt, "sent": bool(ok), "progress": True, "note": "worker progress"}))
            jlog({"event": "progress", "id": tid, "ok": ok, "mins": mins, "out": out[-120:]})

def tick(st, dry, only=None):
    for t in st.load():
        if only and t.get("id") != only: continue
        if t.get("status") not in OPEN: continue
        try:
            if t.get("status") == "pending": handle_pending(st, t, dry)
            else: housekeeping(st, t, dry)
        except Exception as e:
            jlog({"event": "task_error", "id": t.get("id"), "err": str(e)[-200:]})

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry", action="store_true"); ap.add_argument("--once", action="store_true")
    ap.add_argument("--id"); ap.add_argument("--file", default=f"{D}/tasks.jsonl"); ap.add_argument("--interval", type=float, default=2)
    a = ap.parse_args(); st = Store(a.file, a.dry)
    jlog({"event": "worker_start", "dry": a.dry, "pid": os.getpid()})
    while True:
        tick(st, a.dry, a.id)
        if a.once: return
        time.sleep(a.interval)

if __name__ == "__main__": main()
