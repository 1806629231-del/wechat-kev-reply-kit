"""全局发送闸：所有微信发送必须先过 check()，发成功后 record()。状态存盘，跨进程共享。"""
import json, os, re, time, random, fcntl, datetime
D=os.path.dirname(os.path.abspath(__file__))
CFG=os.path.join(D,"config.json"); ST=os.path.join(D,"rate_state.json"); LOCK=os.path.join(D,".gate.lock")
def cfg():
    with open(CFG) as f: return json.load(f)
def send_enabled(): return bool(cfg().get("SEND_ENABLED",False))
def _load():
    try: return json.load(open(ST))
    except Exception: return {"sends":[]}
def trim(msg, is_group=True):
    """只留1–2句，单条消息。"""
    c=cfg(); msg=re.sub(r"\s*\n+\s*"," ",(msg or "").strip())
    parts=re.findall(r".+?(?:[。！？!?…~～]+|$)",msg)
    parts=[p for p in parts if p.strip()][:c["max_sentences"]]
    return "".join(parts).strip()
def check(chat, msg, is_group=True, now=None):
    """返回 (ok, reason)。不写状态。"""
    c=cfg(); now=now or time.time()
    if os.path.exists(c["kill_file"]): return False,"kill_switch"
    h=datetime.datetime.fromtimestamp(now).hour
    if h>=c["quiet_start"] or h<c["quiet_end"]: return False,"quiet_hours"
    if is_group and len(msg)>c["group_max_chars"]: return False,f"too_long({len(msg)}>{c['group_max_chars']})"
    if "\n" in msg.strip(): return False,"multi_chunk"
    s=_load()["sends"]; day=now-86400
    s=[x for x in s if x["t"]>day]
    if s and now-s[-1]["t"]<s[-1].get("gap",c["min_gap_s"]): return False,f"global_gap({int(now-s[-1]['t'])}s)"
    mine=[x for x in s if x["chat"]==chat]
    if mine and now-mine[-1]["t"]<c["per_chat_gap_s"]: return False,"per_chat_gap"
    today=datetime.datetime.fromtimestamp(now).date()
    td=[x for x in s if datetime.datetime.fromtimestamp(x["t"]).date()==today]
    if is_group and sum(x["chat"]==chat for x in td)>=c["per_group_daily"]: return False,"group_daily_cap"
    if len(td)>=c["total_daily"]: return False,"total_daily_cap"
    return True,"ok"
def record(chat, now=None):
    c=cfg(); now=now or time.time()
    with open(LOCK,"w") as lk:
        fcntl.flock(lk,fcntl.LOCK_EX); st=_load()
        st["sends"]=[x for x in st["sends"] if x["t"]>now-86400*2]
        st["sends"].append({"t":now,"chat":chat,"gap":c["min_gap_s"]+random.uniform(*c["gap_jitter_s"])})
        json.dump(st,open(ST,"w"))
def mention_jitter(): time.sleep(random.uniform(*cfg()["mention_jitter_s"]))
