#!/usr/bin/env python3
"""Watch WeChat (DISPLAY :2) for @mentions of {BOT_NICK} and auto-reply.
Run: /workspace/wxauto/.venv/bin/python mention_watch.py [--once] [--interval 30] [--dry]
Reply generation: OpenAI-compatible LLM if LLM_API_KEY (+LLM_BASE_URL, LLM_MODEL) or XAI_API_KEY / OPENAI_API_KEY is set.
On LLM failure: short Chinese fallback reply (standing @-reply permission); never exit daemon for LLM errors.
rc=4 means WeChat is logged out / window missing: the daemon stays alive and
waits for a manual login instead of exiting.  Only screenshots while idle; no keystrokes."""
import os, sys, time, json, re, random, subprocess as sp
os.environ.setdefault("DISPLAY", ":2")
try:
 from dotenv import load_dotenv; load_dotenv("/workspace/wxauto/.env")
except Exception:
 pass
D="/workspace/wxauto"; PY=f"{D}/.venv/bin/python"; ME=os.getenv("BOT_NICK","<机器人微信昵称>"); MES=tuple(x for x in os.getenv("BOT_ALIASES",ME).split(",") if x)
def _me(t): return any(m in t for m in MES)
import mss, numpy as np, PIL.Image as I
from rapidocr_onnxruntime import RapidOCR
eng=RapidOCR(); sys.path.insert(0,D); import send as SND; N=lambda z:z.replace(" ","").lower()
import weflow_read as WF
WEFLOW=os.getenv("WEFLOW_READ","1")!="0"
WF_SEEN_F=f"{D}/weflow_seen.json"
def clean_row_name(s):
    """Strip member count, trailing clock/date, and OCR ellipsis from a chat-list row label."""
    s=(s or "").strip()
    s=re.sub(r"[（(]\d+[)）]\s*$","",s)
    s=re.sub(r"(?:\.{2,}|…+)\s*(?:\d{1,2}:\d{2}|昨天|Yesterday|今天|Today|\d{1,2}[-/年]\d{1,2}(?:日)?).*$","",s,flags=re.I)
    s=re.sub(r"(?:\d{1,2}:\d{2}|昨天|Yesterday|今天|Today)\s*$","",s,flags=re.I)
    s=re.sub(r"(?:\.{2,}|…+)\s*$","",s)
    return s.strip()
def read_chat_header(X,Y,W,H):
    items=ocr(X+314,Y+30,min(400,W-320),60)
    title="".join(t for _,_,t in sorted(items,key=lambda r:r[0])).strip()
    return re.sub(r"[（(]\d+[)）]\s*$","",title).strip()
def now(): return time.strftime("%Y-%m-%dT%H:%M:%S%z")
def jlog(f,o): open(f"{D}/{f}","a").write(json.dumps({"ts":now(),**o},ensure_ascii=False)+"\n")
def win():
    out=sp.run(["xdotool","search","--name","^Weixin$"],capture_output=True,text=True).stdout.split()
    if not out: return None
    g=dict(l.split("=") for l in sp.check_output(["xdotool","getwindowgeometry","--shell",out[0]]).decode().split())
    return out[0],int(g["X"]),int(g["Y"]),int(g["WIDTH"]),int(g["HEIGHT"])
def ocr(X,Y,W,H,save=None):
    with mss.MSS() as s: im=np.array(s.grab({"left":X,"top":Y,"width":W,"height":H}))[:,:,:3]
    if save: I.fromarray(im[:,:,::-1]).save(save)
    r,_=eng(np.ascontiguousarray(im)); return [(int(b[0][0]),int(b[0][1]),t) for b,t,_ in (r or [])]
LOGGED_OUT_STRONG=("scantolog","signedout","loggedout","扫码登录","已退出","switchaccount")
def _looks_logged_out(full):
    """True only for real WeChat login/logout UI — not chat text containing 'login' URLs."""
    if any(N(t)=="send" for *_,t in full): return False
    for *_,t in full:
        n=N(t)
        if any(k in n for k in LOGGED_OUT_STRONG): return True
        if "login" in n or "log in" in t.lower():
            # skip URL-ish OCR (e.g. https://x.com/i/flow/login in a chat bubble)
            if any(x in n for x in ("http","www",".com",".cn","/","flow")): continue
            return True
    return False
MENTION=("[youweremen","[有人@我]","有人@我")
FALLBACK_TMPL="@{sender} 在的在的，方才卡了一下"
def _load_persona():
    """活人语感正文；文件缺失时用短兜底，避免回退成客服腔。"""
    try:
        t=open(f"{D}/persona.md",encoding="utf-8").read().strip()
        if t: return t
    except Exception:
        pass
    return ("你是微信里的熟人「{BOT_NICK}」。短句口语，像真人回微信；"
            "别当客服/AI助手念说明书；不编造{OWNER_NICK}行踪；不透露密钥路径隐私。")
PERSONA=_load_persona()
def _strip_reasoning(r):
    """Drop leaked chain-of-thought; keep a short final reply."""
    if not r: return r
    r=r.strip()
    # common wrappers / tags
    for pat in (r"(?is)<think(?:ing)?>.*?</think(?:ing)?>",
                r"(?is)```(?:reasoning|thinking|thought)\s*.*?```",
                r"(?is)^(?:reasoning|thinking|思考|推理)\s*[:：].*?(?=\n\n|\n最终|\n答案|\n回复|$)"):
        r=re.sub(pat,"",r).strip()
    # if still looks like long reasoning, keep last non-empty sentence/line
    looks=(len(r)>160) or any(k in r.lower() for k in ("let me think","i need to","首先","我需要","分析一下","步骤","step 1","as an ai"))
    if looks:
        parts=[p.strip() for p in re.split(r"[\n。！？!?]+", r) if p.strip()]
        # prefer a short Chinese/English chatty final bit
        for p in reversed(parts):
            if 2<=len(p)<=80 and not any(k in p.lower() for k in ("because","therefore","所以说","综上所述","分析")):
                r=p; break
        else:
            r=parts[-1][:80] if parts else "收到～"
    return r.strip() or "收到～"

def _pane_lines(X,Y,W,H):
    """OCR chat pane -> ordered [(side,text)] side: L=others, R=me, C=system/time."""
    aw=W-380; out=[]
    for x0,x1,y,t in sorted(_ocr_boxes(X+380,Y+85,aw,430),key=lambda b:b[2]):
        t=t.strip()
        if not t: continue
        side="R" if (x1>aw*0.75 and x0>aw*0.35) else "L" if x0<aw*0.3 else "C"
        out.append((side,t))
    return out
def get_context(X,Y,W,H,wid=None,want=18):
    """Recent visible conversation (scroll up once if few lines), as text for the prompt."""
    try:
        cur=_pane_lines(X,Y,W,H)
        if len(cur)<want and wid:
            cx,cy=str(X+380+(W-380)//2),str(Y+250)
            sp.run(["xdotool","mousemove","--sync",cx,cy]); sp.run(["xdotool","click","--repeat","5","4"]); time.sleep(0.35)
            up=_pane_lines(X,Y,W,H)
            sp.run(["xdotool","click","--repeat","15","5"]); time.sleep(0.3)
            # merge: drop overlap (prefix of cur already in up)
            ct=[t for _,t in cur]; ut=[t for _,t in up]; k=0
            for n in range(min(len(ut),len(ct)),0,-1):
                if ut[-n:]==ct[:n]: k=n; break
            cur=up[:len(up)-k]+cur if up!=cur else cur
        lines=[]
        for side,t in cur[-(want*2):]:
            lines.append(("[我/{BOT_NICK}] " if side=="R" else "[系统/时间] " if side=="C" else "")+t)
        return "\n".join(lines[-40:])
    except Exception as e:
        jlog("mention_log.jsonl",{"event":"context_err","err":str(e)[-200:]}); return ""
CTX_RULE=("下面会给出最近的聊天记录（OCR识别，按时间从上到下；群聊里气泡上方的短行通常是发言人昵称；标[我/{BOT_NICK}]的是你自己之前发的）。"
          "务必结合上下文回复：看清大家在聊什么、之前有谁问过什么、有什么梗，回复要接得上话题，别只孤立地回最后一句；"
          "上下文只用来理解，不要复述聊天记录，也不要从中推测或编造{OWNER_NICK}的行踪、计划。")
TASK_RULE=("\n\n【输出格式】只输出一个JSON对象，不要别的文字："
  '{"type":"chat"或"task","reply":"要发出的回复","task":"任务描述(仅task时)","deliverable":"要交付的具体东西，如「10个选项的清单」「一段语音」「一张图片」(仅task时，说不出具体交付物就是chat)"}。'
  "只有对方明确要求你做事（帮我/给我/罗列/查/整理/写/找/翻译/总结/推荐/发我…）才是task；陈述、评论、感叹、图片、表情、文件、链接本身都是chat。"
  "type=task：对方要你真正去做事——查资料、搜索、调研、总结整理、盯着/监控某事、制作或产出东西（文档、图片、代码、清单等）。"
  "type=chat：闲聊、打招呼、斗图、表情包、简单问答（凭常识能直接答的）。"
  "task时：reply只写一句口语确认，如「行，我去弄，好了喊你」，不要假装已经做完、不要编结果；"
  "task字段用一句完整中文写清要做什么（含必要的对象/范围，结合上下文补全指代）。chat时task留空字符串。")
ACK_DEFAULT="行，我去弄，好了在这回你"
LAST_TASK=None; LAST_CLS={}
KEV_TASK_MIN=0.75
def kev_cls(text,context=""):
    """kev chat/task vote -> (label, prob) or (None, None) on failure."""
    try:
        st=(f"最近聊天:\n{context[-800:]}\n\n" if context else "")+f"新消息: {text}"
        r=decide.choice(st,"Is the new message chit-chat/simple Q&A/stickers (chat), or a real TASK asking the assistant to look something up, research, summarize, monitor, or make/produce something (task)?",["chat","task"])["answers"]["q"]
        lab=r["choice"]; return lab, float(r.get("probabilities",{}).get(lab,r.get("confidence",0)))
    except Exception as e:
        return None, None
def _parse_cls(r):
    """Parse classifier JSON; returns (type, reply, task). Non-JSON -> chat with raw text."""
    t=re.sub(r"(?s)^```(?:json)?\s*|\s*```$","",(r or "").strip())
    m=re.search(r"(?s)\{.*\}",t)
    if m:
        try:
            o=json.loads(m.group(0))
            dv=str(o.get("deliverable","")).strip()
            ty="task" if (str(o.get("type","")).lower()=="task" and str(o.get("task","")).strip()
                          and len(dv)>=2 and not re.search(r"^(无|没有|不明|未知|none|n/?a)$",dv,re.I)) else "chat"
            return ty,str(o.get("reply","")).strip(),str(o.get("task","")).strip()
        except Exception: pass
    return "chat",r,""
_VOICE_RE=re.compile(r"语音|说一[个句段遍下]|念一?[个句段遍下]?|读给我听|用声音|讲一[个句段]|绕口令")
def add_task(chat,is_group,sender,task,context,src_text):
    import uuid, fcntl
    tid=time.strftime("%m%d%H%M")+"-"+uuid.uuid4().hex[:4]
    o={"id":tid,"chat":chat,"is_group":bool(is_group),"sender":sender or "","task":task,"text":src_text,
       "context":[l for l in (context or "").split("\n") if l.strip()][-30:],"ts":now(),"status":"pending","updates":[]}
    with open("/tmp/wx_tasks.lock","w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        with open(f"{D}/tasks.jsonl","a") as f:
            f.write(json.dumps(o,ensure_ascii=False)+"\n"); f.flush(); os.fsync(f.fileno())
    return tid
_PROMISE_RE=re.compile(r"马上就好|稍等|稍后|进度|同步给|整理好|弄好|做好了|快好了|在做|在弄|在查|这就去|发这里")
NO_TASK_REPLY="我这边没有在跑的任务哦，需要的话直接@我说一下要做啥～"
def _has_pending_task(chat):
    try:
        for l in open(f"{D}/tasks.jsonl"):
            try: o=json.loads(l)
            except Exception: continue
            if o.get("chat")==chat and o.get("status") in ("pending","running","in_progress"): return True
    except FileNotFoundError: pass
    return False
_REQ_RE=re.compile(r"发我|发一?[句段个张首条]|来一?[句段个张首]|给我|帮我|帮忙|麻烦|列一?下|列出|罗列|列个|查一?下|查查|搜|整理|做一?个|做个|写一?个|写个|写一?段|写首|找一?下|找找|翻译|总结|概括|生成|画|推荐|对比|分析|算一?下|计算|统计|监控|盯")
_MEDIA_RE=re.compile(r"^\s*(\[(图片|表情|动画表情|文件|语音|视频|链接|位置|名片|小程序|聊天记录|图片/表情包|未识别[^\]]*)\][^\n]*)?\s*$")
def _req_part(text):
    """The part of a message that can carry a request: drop quoted original after ｜ 引：, drop pure media markers."""
    t=(text or "")
    t=re.split(r"\s*｜\s*引[:：]",t)[0]
    t=re.sub(r"^\[引用\]\s*","",t)
    return t.strip()
def _is_media_only(text):
    t=_req_part(text)
    return (not t) or bool(_MEDIA_RE.match(t)) or bool(re.fullmatch(r"\s*(https?://\S+)\s*",t))
_STATUS_RE=re.compile(r"好了[吗没嘛么]|要多久|多久|还要多久|进度|完成了[吗没]|做完了?[吗没]|搞定了?[吗没]|怎么样了")
DRY_MODE=False
LAST_TASK_ID=None
ACK_STARTED="行，我去弄，好了喊你"
NO_ENQUEUE_REPLY="这个我这会儿没法记下来，稍后再@我一次哈～"
def _tasks_for(chat):
    out=[]
    try:
        for l in open(f"{D}/tasks.jsonl"):
            try: o=json.loads(l)
            except Exception: continue
            if o.get("chat")==chat: out.append(o)
    except FileNotFoundError: pass
    return out
def _status_reply(chat):
    ts=_tasks_for(chat)
    if not ts: return "我这边没有你的任务记录哦，要做啥直接@我说～"
    act=[t for t in ts if t.get("status") not in ("done","failed","cancelled")]
    if not act:
        t=ts[-1]; return f"上一个任务「{t.get('task','')[:24]}」已经做完发过啦，现在没有在跑的～"
    t=act[-1]; st=t.get("status")
    try: mins=max(0,int((time.time()-time.mktime(time.strptime(t["ts"][:19],"%Y-%m-%dT%H:%M:%S")))/60))
    except Exception: mins=0
    lab={"pending":"刚排上队","running":"正在做","needs_agent":"需要查资料，正在处理"}.get(st,st)
    up=(t.get("updates") or [])
    last=(up[-1].get("note") or up[-1].get("text") or "") if up and isinstance(up[-1],dict) else ""
    return f"「{t.get('task','')[:24]}」{lab}，已经{mins}分钟了"+(f"，最新：{last[:30]}" if last else "")
def llm(chat,sender,text,private=False,context=""):
    global LAST_TASK,LAST_CLS,LAST_TASK_ID
    LAST_TASK=None; LAST_CLS={}; LAST_TASK_ID=None
    if _STATUS_RE.search(_req_part(text)) and not _REQ_RE.search(_req_part(text)) and _tasks_for(chat):
        LAST_CLS={"cls":"status"}
        return ("" if private else f"@{sender} ")+_status_reply(chat)
    key=os.getenv("LLM_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key: return None
    base=os.getenv("LLM_BASE_URL") or ("https://api.x.ai/v1" if os.getenv("XAI_API_KEY") else "https://api.openai.com/v1")
    model=os.getenv("LLM_MODEL") or ("grok-3-mini" if "x.ai" in base else "gpt-4o-mini")
    import urllib.request, urllib.error
    sysmsg=("【场景】微信群里，有人 @ 你（昵称「{BOT_NICK}」）。用中文回，直接给最终回复，不要思考过程。"
            "字数尽量 ≤60；不确定的事实别编，可以说回头查。"
            "\n\n"+PERSONA+"\n\n"
            "别人要表情包/{STICKER_TOPIC}/发图：口头接一下即可，绝不要说发不了图。")
    if private:
        _owner_dm = any(k in (chat or "") for k in ("{OWNER_NICK}","{OWNER_NICK}","{OWNER_NICK}"))
        if _owner_dm:
            sysmsg=("【场景】和{OWNER_NICK}本人私聊。用中文回，直接给最终回复，不要思考过程。字数尽量 ≤50。"
                    "对他直接说话，别用第三人称提他，更不要说「去问{OWNER_NICK}」。不泄露密钥路径隐私。"
                    "\n\n"+PERSONA+"\n\n"
                    "文件/链接：随口认一下就行，别每条都问要不要帮忙；他让你做再做。"
                    "图片/表情当斗图接一句即可，系统会再贴；别说看不到图。")
        else:
            sysmsg=("【场景】微信私聊（对方是{OWNER_NICK}的联系人，不是他本人）。用中文回，直接给最终回复，不要思考过程。"
                    "字数尽量 ≤50。"
                    "你只代表自己说话：不知道{OWNER_NICK}此刻在哪/忙不忙，禁止说他不在/在忙/过会儿回；"
                    "禁止承诺转告或替他安排；不确定就让对方直接问{OWNER_NICK}；不泄露密钥路径隐私。"
                    "\n\n"+PERSONA+"\n\n"
                    "若对方发来图片/表情（消息含[图片]/[动画表情]/[图片/表情包]或几乎无字）：当斗图接一句即可，"
                    "系统会再贴表情；绝不要说看不到图。")
    umsg=(f"私聊对象：{chat}\n对方最新消息：{text}" if private else f"群：{chat}\n{sender} @你说：{text}\n请针对这条@消息、结合上下文回复。")
    sysmsg+=TASK_RULE
    if context:
        sysmsg+=CTX_RULE
        umsg=f"【最近聊天记录】\n{context}\n【记录结束】\n\n"+umsg
    body=json.dumps({"model":model,"messages":[{"role":"system","content":sysmsg},
        {"role":"user","content":umsg}],"max_tokens":220,"temperature":0.9,"thinking":{"type":"disabled"},"reasoning_effort":"none"}).encode()
    headers={"Authorization":"Bearer "+key,"Content-Type":"application/json","User-Agent":"curl/8.5.0"}
    last=None
    for attempt in range(3):
        rq=urllib.request.Request(base.rstrip("/")+"/chat/completions",body,headers)
        try:
            with urllib.request.urlopen(rq,timeout=30) as resp:
                msg=json.load(resp)["choices"][0]["message"]
            r=(msg.get("content") or "").strip()
            ty,rr,tk=_parse_cls(r)
            kl,kp=kev_cls(text,context); final=ty
            # Task iff a real request aimed at the bot: request words OR LLM-task with a concrete deliverable.
            # kev alone never makes a task; media/stickers/files/links alone are always chat.
            rp=_req_part(text); media=_is_media_only(text)
            req=(not media) and bool(_REQ_RE.search(rp))
            if (not media) and _VOICE_RE.search(rp): req=True  # voice requests -> task_worker voice path
            final="task" if (not media and (req or ty=="task")) else "chat"
            LAST_CLS={"kev":kl,"kev_p":kp,"llm":ty,"cls":final,"req_word":req,"media":media}
            if final=="task" and not tk: tk=rp[:300] or text.strip()[:300]
            ty=final
            if ty=="task":
                LAST_TASK=tk
                try:
                    LAST_TASK_ID=add_task(chat, not private, sender, tk, context, (text or "")[-500:]) if not DRY_MODE else "dry"
                    r=ACK_STARTED
                except Exception as e:
                    LAST_TASK=None; LAST_TASK_ID=None; r=NO_ENQUEUE_REPLY; LAST_CLS["enqueue_err"]=str(e)[:120]
                LAST_CLS["task_id"]=LAST_TASK_ID
            else: r=rr
            r=_strip_reasoning(r)
            if ty!="task" and _PROMISE_RE.search(r) and not _has_pending_task(chat):
                r=NO_TASK_REPLY; LAST_CLS["promise_guard"]=True
            if not private and sender: r=re.sub(r'^(?:@'+re.escape(sender)+r'\s*)+','',r).strip()
            r=re.sub(r"\s*[—-]{1,2}\s*{BOT_NICK}[^\n]*$","",r).strip()
            return ("" if private else f"@{sender} ") + r 
        except Exception as e:
            last=e
            es=str(e).lower()
            code=getattr(e,"code",None)
            retryable=(code in (429,500,502,503,504) or "timed out" in es or "timeout" in es
                       or "503" in es or "502" in es or "429" in es or "500" in es
                       or isinstance(e, TimeoutError) or (isinstance(e, urllib.error.URLError) and ('timed out' in es or 'timeout' in es or 'temporary' in es or '503' in es)))
            if attempt<2 and retryable:
                time.sleep(0.5*(attempt+1)); continue
            raise
    raise last
CAT=json.load(open(f"{D}/stickers/catalog.json"))
sys.path.insert(0,"/workspace/kev"); import decide
STICKER_ASK_RE=re.compile(r"\[图片|\[动画表情|\[Sticker|\[Photo|表情包|{STICKER_TOPIC}|发图|发个图|发张图|贴纸|sticker|gpt娘|deepseek|{STICKER_TOPIC}", re.I)
TOPIC_KEYS=(
    ("{STICKER_TOPIC}",("{STICKER_TOPIC}","deepseek","{STICKER_TOPIC}","ds")),
    ("gpt",("gpt娘","gpt","chatgpt")),
    ("claude",("claude","克劳德")),
)
def _wants_sticker(text):
    return bool(STICKER_ASK_RE.search(text or ""))
def _topic_needles(text):
    t=(text or "").lower()
    needles=[]
    for _,keys in TOPIC_KEYS:
        if any(k.lower() in t for k in keys):
            needles.extend(keys)
    return needles
def _sticker_pool(limit=12, prefer_needles=None):
    """Diverse ≤limit stickers; prefer those whose desc/id match topic needles."""
    prefer_needles=[n.lower() for n in (prefer_needles or [])]
    preferred=[]
    if prefer_needles:
        for c in CAT:
            blob=(c.get("desc","")+c.get("id","")+c.get("category","")).lower()
            if any(n in blob for n in prefer_needles):
                preferred.append(c)
    by={}
    for c in CAT: by.setdefault(c["category"], []).append(c)
    cats=list(by.keys()); random.shuffle(cats)
    picks=[]; seen=set()
    # fill from preferred first (shuffle), then diverse categories
    pref=preferred[:]; random.shuffle(pref)
    for c in pref:
        if c["id"] in seen: continue
        picks.append(c); seen.add(c["id"])
        if len(picks)>=limit: return picks
    for cat in cats:
        items=by[cat][:]; random.shuffle(items)
        for c in items:
            if c["id"] in seen: continue
            picks.append(c); seen.add(c["id"]); break
        if len(picks)>=limit: return picks
    rest=[c for c in CAT if c["id"] not in seen]; random.shuffle(rest)
    for c in rest:
        picks.append(c)
        if len(picks)>=limit: break
    return picks
def maybe_sticker(chat,text,reply,dry):
    t0=time.perf_counter()
    try:
        force=_wants_sticker(text)
        needles=_topic_needles(text)
        st=f"群聊消息：{text}\n我的文字回复：{reply}"
        p=1.0; t_noul=t0
        if not force:
            k=decide.noul(st,"Would a cute meme sticker after this reply fit (anything except very serious or sad topics)?")
            p=k["answers"]["q"]["noul"]; t_noul=time.perf_counter()
            if p<0.25:
                return {"sticker":None,"noul":p,"force":False,"decide_ms":round((t_noul-t0)*1000),"send_image_ms":0}
        else:
            t_noul=time.perf_counter()
        # meaning-matched pick (sticker_pick.py): DeepSeek picks from top-8, hostile never, NONE allowed
        import sticker_pick as SP
        sel,pinfo=SP.pick(reply,text,k=8,forced=bool(force or needles))
        t_dec=time.perf_counter()
        if not sel:
            return {"sticker":None,"noul":p,"force":force,"pick":pinfo,"decide_ms":round((t_dec-t0)*1000),"send_image_ms":0}
        path=sel["path"]; sid=sel["id"]
        t_send0=time.perf_counter()
        ok,lg=SND.send_image(chat,path,dry=dry,eng=eng,already_open=True,fast=True)
        t_send1=time.perf_counter()
        return {"sticker":sid,"noul":p,"force":force,"ok":ok,"out":str(lg)[-200:],
                "decide_ms":round((t_dec-t0)*1000),"send_image_ms":round((t_send1-t_send0)*1000),
                "noul_ms":round((t_noul-t0)*1000),"choice_ms":round((t_dec-t_noul)*1000)}
    except Exception as e:
        return {"sticker_err":str(e)[-200:],"decide_ms":round((time.perf_counter()-t0)*1000)}
import harvest as HV
def _harvest_pane(X,Y,W,H,tag=""):
    """Grab chat pane, save new left-side stickers to pending. Returns (new_count, n_images_seen)."""
    try:
        with mss.MSS() as s: im=np.array(s.grab({"left":X+320,"top":Y+85,"width":W-330,"height":435}))[:,:,:3]
        rgb=np.ascontiguousarray(im[:,:,::-1])
        seen=len(HV.find_images(rgb)); n=HV.harvest(rgb,tag)
        if n: jlog("mention_log.jsonl",{"event":"stickers_harvested","n":n,"chat":tag})
        return n,seen
    except Exception as e:
        jlog("mention_log.jsonl",{"event":"harvest_err","err":str(e)[-200:]}); return 0,0
def _click_chat_row(wid,X,Y,name):
    sp.run(["xdotool","windowactivate","--sync",wid])
    sp.run(["xdotool","mousemove","--sync",str(X+name[0]+40),str(Y+name[1]+8),"click","1"])

def _chat_pane_ready(area,hdr):
    """True when chat pane looks loaded: @{BOT_NICK} visible, or header + message-like text."""
    if any(_me(r[2].replace(" ","")) for r in area):
        return True
    if hdr and len(N(hdr))>=2 and len(area)>=2:
        return True
    return False

def _wait_chat_pane(X,Y,W,H,timeout=3.0,interval=0.25):
    """Poll chat-area OCR until pane loaded or timeout. Returns (area, hdr, shot, found_me)."""
    deadline=time.time()+timeout
    area,hdr,shot,found_me=[], "", None, False
    while True:
        shot=f"{D}/mention_{int(time.time())}.png"
        area=ocr(X+380,Y+85,W-380,480,save=shot)
        hdr=read_chat_header(X,Y,W,H)
        found_me=any(_me(r[2].replace(" ","")) for r in area)
        if found_me or _chat_pane_ready(area,hdr):
            return area,hdr,shot,found_me
        if time.time()>=deadline:
            return area,hdr,shot,found_me
        time.sleep(interval)

def once_mention(dry,w,full):
    wid,X,Y,W,H=w
    rows=sorted([r for r in full if 110<=r[0]<=300 and r[1]>=85],key=lambda r:r[1])
    hit=next((r for r in rows if any(k in N(r[2]) for k in MENTION)),None)
    if not hit: return 0
    name=next((r for r in reversed(rows) if r[1]<hit[1] and hit[1]-r[1]<40 and not re.match(r"^\d",r[2])),None)
    if not name: return 0
    chat=clean_row_name(name[2])
    _click_chat_row(wid,X,Y,name)
    area,hdr,shot,found_me=_wait_chat_pane(X,Y,W,H,timeout=3.0,interval=0.25)
    # Prefer the open-chat header (full name) over truncated list-row OCR
    if hdr and len(N(hdr))>=2: chat=hdr
    _harvest_pane(X,Y,W,H,chat)
    ms=[r for r in area if _me(r[2].replace(" ",""))]
    if not ms:
        # Chat pane may still be placeholder — re-click once and poll again
        jlog("mention_log.jsonl",{"event":"chat_pane_retry_click","chat":chat,"shot":shot})
        _click_chat_row(wid,X,Y,name)
        area,hdr,shot,found_me=_wait_chat_pane(X,Y,W,H,timeout=3.0,interval=0.25)
        if hdr and len(N(hdr))>=2: chat=hdr
        ms=[r for r in area if _me(r[2].replace(" ",""))]
    if not ms:
        jlog("mention_log.jsonl",{"event":"mention_not_found","chat":chat,"shot":shot,"force_rescan":True})
        return 2  # signal main loop to rescan soon (do not wait for db change)
    m=ms[-1]; text=" ".join(t for x,y,t in area if abs(y-m[1])<60 and y>=m[1] and x<m[0]+30) or m[2]
    sn=[r for r in area if r[1]<m[1] and m[1]-r[1]<40 and r[0]<m[0]+10]; sender=sn[-1][2] if sn else ""
    ev={"chat":chat,"sender":sender,"text":text,"shot":shot}
    # de-dup: never answer the same mention twice
    seen=f"{D}/mention_log.jsonl"
    if os.path.exists(seen) and any(json.loads(l).get("event")=="replied" and json.loads(l).get("chat")==chat and json.loads(l).get("text")==text for l in open(seen)):
        return 0
    t0=time.perf_counter(); used_fallback=False
    ctx=get_context(X,Y,W,H,wid); ev["ctx_lines"]=ctx.count("\n")+1 if ctx else 0
    try: reply=llm(chat,sender,text,context=ctx)
    except Exception as e:
        jlog("mention_log.jsonl",{"event":"llm_error","err":str(e)[:200],**ev}); reply=None
    if not reply:
        # standing @-reply: never queue/exit — short fixed Chinese fallback
        reply=FALLBACK_TMPL.format(sender=sender or "你")
        used_fallback=True
        jlog("mention_log.jsonl",{"event":"llm_fallback",**ev})
    t1=time.perf_counter()
    try:
        ok,lg=SND.send(chat,reply,dry=dry,eng=eng,already_open=True,before=0,mention=sender or None); out=str(lg)
        evn=("dry_ok" if dry else "replied" if ok else "send_failed")
    except Exception as e: evn,out,ok="send_failed",str(e)[-300:],False
    task=LAST_TASK if not used_fallback else None; ev.update(LAST_CLS)
    if task and evn in("replied","dry_ok"):
        ev["task_id"]=LAST_TASK_ID
        ev["task"]=task; stk={}
    else: stk=maybe_sticker(chat,text,reply,dry) if evn in("replied","dry_ok") else {}
    jlog("mention_log.jsonl",{"event":evn,"reply":reply,"out":out[-400:],"llm_ms":round((t1-t0)*1000),"send_ms":round((time.perf_counter()-t1)*1000),"fallback":used_fallback,**stk,**ev})
    if used_fallback and evn in("replied","dry_ok"):
        open(f"{D}/mentions.jsonl","w").close()
        jlog("mention_log.jsonl",{"event":"mentions_cleared_after_fallback"})
    return 0
# ---------------- private (1:1) chat auto-reply ----------------
import hashlib
KNOWN_GROUPS=tuple(g for g in os.getenv("WX_GROUPS","").split(",") if g)
OFFICIAL=("tencentnews","weixinteam","serviceacc","filetransfer","公众号","服务号","服务通知","腾讯新闻","微信团队","文件传输",
          "订阅号","微信支付","weixinpay","wechatpay","微信运动","wechatsport","qq邮箱","qqmail","subscriptions")
DM_STATE=f"{D}/dm_state.json"
def _dm_state():
    try: return json.load(open(DM_STATE))
    except Exception: return {}
def _dm_save(st):
    tmp=DM_STATE+".tmp"; json.dump(st,open(tmp,"w"),ensure_ascii=False); os.replace(tmp,DM_STATE)
def _badge_rows(X,Y):
    """y (window-relative) of red unread badges on chat-list avatars."""
    with mss.MSS() as s: im=np.array(s.grab({"left":X+100,"top":Y+85,"width":28,"height":560}))[:,:,:3]
    b,g,r=im[:,:,0].astype(int),im[:,:,1].astype(int),im[:,:,2].astype(int)
    m=((r>200)&(g<90)&(b<90)).sum(axis=1)
    ys=[]; i=0
    while i<len(m):
        if m[i]>=3:
            j=i
            while j<len(m) and m[j]>=1: j+=1
            if m[i:j].sum()>=40: ys.append(85+(i+j)//2)
            i=j
        else: i+=1
    return ys
def _ocr_boxes(X,Y,W,H):
    with mss.MSS() as s: im=np.array(s.grab({"left":X,"top":Y,"width":W,"height":H}))[:,:,:3]
    r,_=eng(np.ascontiguousarray(im))
    return [(int(min(p[0] for p in b)),int(max(p[0] for p in b)),int(min(p[1] for p in b)),t) for b,t,_ in (r or [])]
def _raw_header(X,Y,W):
    items=ocr(X+314,Y+30,min(400,W-320),60)
    return "".join(t for _,_,t in sorted(items,key=lambda r:r[0])).strip()
def once_dm(dry,w,full):
    wid,X,Y,W,H=w
    badges=_badge_rows(X,Y)
    if not badges: return 0
    rows=[r for r in full if 120<=r[0]<=300 and r[1]>=85]
    st=_dm_state(); acted=0
    for by in badges:
        nm=[r for r in rows if by-8<=r[1]<=by+14 and not re.match(r"^\d",r[2])]
        rowname=clean_row_name(nm[0][2]) if nm else ""
        nr=N(rowname)
        if any(k in nr for k in OFFICIAL) or any(N(g) in nr for g in KNOWN_GROUPS) or st.get("_groups",{}).get(nr):
            continue
        # open the row (badge y + 12 ≈ row name line)
        sp.run(["xdotool","windowactivate","--sync",wid])
        sp.run(["xdotool","mousemove","--sync",str(X+200),str(Y+by+14),"click","1"]); time.sleep(0.6)
        raw=_raw_header(X,Y,W)
        if re.search(r"[（(]\d+[)）]\s*$",raw) or any(N(g) in N(raw) for g in KNOWN_GROUPS):
            if nr: st.setdefault("_groups",{})[nr]=1; _dm_save(st)
            continue
        if any(k in N(raw) for k in OFFICIAL): continue
        chat=raw or rowname
        aw=W-380; boxes=[]
        for _ in range(8):
            boxes=_ocr_boxes(X+380,Y+85,aw,430)
            if boxes: break
            time.sleep(0.25)
        boxes.sort(key=lambda b:b[2])
        inc=[]; 
        for x0,x1,y,t in boxes:
            if x1>aw*0.75 and x0>60: inc=[]; continue          # our own (right) bubble: reset
            if x0<aw*0.25: inc.append(t); continue              # incoming (left) bubble
            if "拍了拍" in t and "我" in t: inc.append(t)        # centered pat notice
            # other centered lines (time stamps / system) ignored
        _hn,_himgs=_harvest_pane(X,Y,W,H,chat)
        text=" ".join(inc).strip()
        if _himgs and len(N(text))<=6: text=(text+" [图片/表情包]").strip()
        if not text: jlog("mention_log.jsonl",{"event":"dm_no_incoming","chat":chat}); continue
        key=N(chat) or nr or "dm_unknown"
        h=hashlib.sha1(N(text).encode()).hexdigest()[:16]
        if st.get(key)==h: continue
        ev={"chat":chat,"row":rowname,"text":text[-500:]}
        t0=time.perf_counter(); fb=False
        ctx=get_context(X,Y,W,H,wid); ev["ctx_lines"]=ctx.count("\n")+1 if ctx else 0
        try: reply=llm(chat,chat,text[-600:],private=True,context=ctx)
        except Exception as e: jlog("mention_log.jsonl",{"event":"llm_error","err":str(e)[:200],**ev}); reply=None
        task=LAST_TASK; ev.update(LAST_CLS)
        if not reply: reply="在的，收到～（稍慢了一拍）"; fb=True; task=None
        t1=time.perf_counter()
        try:
            ok,lg=SND.send(chat,reply,dry=dry,eng=eng,already_open=True,before=0,fast=True); out=str(lg)
            # long replies wrap so bubble-text verify is unreliable; keypress reached = sent
            if not ok and "SEND keypress" in out: ok=True
            evn=("dm_dry_ok" if dry else "dm_replied" if ok else "dm_send_failed")
        except Exception as e: evn,out,ok="dm_send_failed",str(e)[-300:],False
        if evn in("dm_replied","dm_dry_ok") and not dry:
            st[key]=h; _dm_save(st)
        if task and evn in("dm_replied","dm_dry_ok"):
            ev["task_id"]=LAST_TASK_ID
            ev["task"]=task; stk={}
        else: stk=maybe_sticker(chat,text,reply,dry) if evn in("dm_replied","dm_dry_ok") else {}
        jlog("mention_log.jsonl",{"event":evn,"reply":reply,"out":out[-300:],"llm_ms":round((t1-t0)*1000),"send_ms":round((time.perf_counter()-t1)*1000),"fallback":fb,**stk,**ev})
        acted+=1
    return 0

# ---------------- weflow (DB decrypt) message reading ----------------
def _wf_load_seen():
    try:
        d=json.load(open(WF_SEEN_F))
        return d.get("baseline",0), set(d.get("seen",[]))
    except Exception:
        return 0, set()
def _wf_save_seen(baseline, seen):
    tmp=WF_SEEN_F+".tmp"
    json.dump({"baseline":baseline,"seen":list(seen)[-8000:]},open(tmp,"w"),ensure_ascii=False)
    os.replace(tmp,WF_SEEN_F)
def _wf_context(msgs, want=12):
    """Format last `want` messages (oldest->newest) for the LLM prompt."""
    lines=[]
    for m in msgs[-want:]:
        t=(m.get("parsedContent") or m.get("content") or "").strip()
        if not t: continue
        if int(m.get("isSend") or 0)==1:
            lines.append("[我/{BOT_NICK}] "+t)
        else:
            sd=(m.get("senderDisplay") or "").strip()
            lines.append((sd+": " if sd else "")+t)
    return "\n".join(lines[-want*2:])
def _wf_type_text(nm):
    """Map a non-text message to a marker the downstream prompt understands."""
    lt=nm.get("local_type")
    t=nm.get("text") or ""
    if t and not t.startswith("[未识别"): return t
    if lt==3: return "[图片]"
    if lt==47: return "[动画表情]"
    if lt==34: return "[语音]"
    return t
RETRY_F=f"{D}/weflow_retry.json"; RETRY_MAX=2
def _retry_load():
    try: return json.load(open(RETRY_F))
    except Exception: return {}
def _retry_save(d):
    tmp=RETRY_F+".tmp"; json.dump(d,open(tmp,"w"),ensure_ascii=False); os.replace(tmp,RETRY_F)
def _retry_add(mid, chat, chat_user, reply, mention):
    d=_retry_load()
    if mid not in d: d[mid]={"chat":chat,"chat_user":chat_user,"reply":reply,"mention":mention,"attempts":1,"ts":now()}
    _retry_save(d)
def _retry_pass(dry):
    """Resend failed replies only (never re-run llm/enqueue); give up after RETRY_MAX extra attempts."""
    if dry: return
    d=_retry_load(); ch=False
    for mid,r in list(d.items()):
        if r.get("attempts",0)>RETRY_MAX:
            jlog("mention_log.jsonl",{"event":"send_gave_up","msg_id":mid,"chat":r["chat"]}); d.pop(mid); ch=True; continue
        r["attempts"]=r.get("attempts",0)+1; ch=True
        try:
            ok,lg=SND.send(r["chat"], r["reply"], eng=eng, mention=r.get("mention"), chat_user=r.get("chat_user"))
            if not ok and "SEND keypress" in str(lg): ok=True
        except Exception as e: ok,lg=False,str(e)[-200:]
        jlog("mention_log.jsonl",{"event":"retry_sent" if ok else "retry_failed","msg_id":mid,"chat":r["chat"],"attempt":r["attempts"],"out":str(lg)[-200:]})
        if ok: d.pop(mid)
    if ch: _retry_save(d)
def once_weflow(dry, w):
    """Read new messages via weflow; reply using the same downstream logic.
    Raises on weflow failure so the caller can fall back to OCR."""
    t_read0=time.perf_counter()
    sess=WF.sessions(limit=20)                       # raises on weflow error
    baseline, seen = _wf_load_seen()
    first_run = baseline==0
    now_ts=int(time.time())
    # Pull messages per relevant session
    per_chat={}
    for s in sess:
        user=s.get("username") or ""
        disp=s.get("displayName") or s.get("nickname") or user
        if not user: continue
        if WF.is_official(user, disp): continue
        try:
            raw=WF.messages(user, n=20)
        except Exception as e:
            jlog("mention_log.jsonl",{"event":"weflow_msgs_err","chat":disp,"err":str(e)[-160:]})
            continue
        per_chat[user]=(disp, raw)
    read_ms=round((time.perf_counter()-t_read0)*1000)
    # First run after switch: seed baseline + mark all current ids seen (never answer backlog)
    if first_run:
        for user,(disp,raw) in per_chat.items():
            for m in raw:
                seen.add(WF.msg_id(user,m))
        baseline=now_ts
        if not dry: _wf_save_seen(baseline, seen)
        jlog("mention_log.jsonl",{"event":"weflow_baseline_seeded","chats":len(per_chat),"seen":len(seen),"read_ms":read_ms})
        return read_ms
    # Build actionable list: new, incoming, not seen, newer than baseline
    actioned=0
    for user,(disp,raw) in per_chat.items():
        norm=[WF.normalize(m,user,disp) for m in raw]
        grp=WF.is_group(user)
        for i,nm in enumerate(norm):
            if nm["id"] in seen: continue
            if nm["is_send"]: 
                seen.add(nm["id"]); continue
            if nm["create_time"] < baseline: 
                seen.add(nm["id"]); continue
            if grp and not nm["is_at_me"]:
                seen.add(nm["id"]); continue
            # actionable
            raw_text=_wf_type_text(nm)
            text=WF.strip_mention_prefix(raw_text) if grp else raw_text
            if not (text or "").strip():
                seen.add(nm["id"]); continue
            # Idempotency: claim this message id BEFORE llm/enqueue/send and persist now,
            # so a failed send / crash / restart can never re-enqueue it. Failed sends go to a bounded retry list.
            seen.add(nm["id"])
            if not dry: _wf_save_seen(baseline, seen)
            ctx=_wf_context(raw[:i+1], want=12)
            ev={"chat":disp,"msg_id":nm["id"],"sender":nm["sender"],"text":text[-500:],"src":"weflow","is_group":grp}
            t0=time.perf_counter(); fb=False
            try:
                reply=llm(disp, nm["sender"] if grp else disp, text[-600:], private=(not grp), context=ctx)
            except Exception as e:
                jlog("mention_log.jsonl",{"event":"llm_error","err":str(e)[:200],**ev}); reply=None
            task=LAST_TASK; cls=dict(LAST_CLS)
            if not reply:
                reply=(FALLBACK_TMPL.format(sender=nm["sender"] or "你") if grp else "在的在的，方才卡了一下")
                fb=True; task=None
            t1=time.perf_counter()
            try:
                ok,lg=SND.send(disp, reply, dry=dry, eng=eng, already_open=False, before=None,
                               mention=(nm["sender"] if grp else None), chat_user=user)
                out=str(lg)
                if not ok and "SEND keypress" in out: ok=True
                evn=("weflow_dry_ok" if dry else "replied" if ok else "send_failed")
            except Exception as e:
                evn,out,ok="send_failed",str(e)[-300:],False
            if evn in("replied","weflow_dry_ok"):
                if not dry: seen.add(nm["id"])
                if task and not fb:
                    ev["task_id"]=LAST_TASK_ID
                    ev["task"]=task; stk={}
                else:
                    stk=maybe_sticker(disp, text, reply, dry)
            else:
                stk={}
                if not dry: _retry_add(nm["id"], disp, user, reply, (nm["sender"] if grp else None))
            jlog("mention_log.jsonl",{"event":evn,"reply":reply,"out":out[-400:],
                 "llm_ms":round((t1-t0)*1000),"send_ms":round((time.perf_counter()-t1)*1000),
                 "fallback":fb,"read_ms":read_ms,**cls,**stk,**ev})
            actioned+=1
    if not dry: _wf_save_seen(baseline, seen)
    _retry_pass(dry)
    if actioned==0:
        jlog("mention_log.jsonl",{"event":"weflow_no_action","read_ms":read_ms})
    return read_ms

def once(dry):
    global DRY_MODE; DRY_MODE=bool(dry)
    w=win()
    if not w: jlog("mention_log.jsonl",{"event":"no_window"}); return 4
    sp.run(["xdotool","windowraise",w[0]])  # other windows (e.g. Chrome) covering WeChat made OCR blind
    wid,X,Y,W,H=w; full=ocr(X,Y,W,H)
    if _looks_logged_out(full):
        jlog("mention_log.jsonl",{"event":"logged_out","texts":[t for *_,t in full][:10]}); return 4
    # Primary reading path: weflow (DB decrypt). Fall back to OCR only on error.
    if WEFLOW:
        try:
            once_weflow(dry, w)
            return 0
        except Exception as e:
            jlog("mention_log.jsonl",{"event":"weflow_error_fallback_ocr","err":str(e)[-300:]})
    rc=once_mention(dry,w,full)
    try:
        full2=ocr(X,Y,W,H) if rc!=0 or True else full
        once_dm(dry,w,full2)
    except Exception as e:
        jlog("mention_log.jsonl",{"event":"dm_error","err":str(e)[-300:]})
    return rc
import glob
WATCH=glob.glob(os.path.expanduser("~/xwechat_files/wxid_*/db_storage/message/message_*.db*"))+glob.glob(os.path.expanduser("~/xwechat_files/wxid_*/db_storage/session/session.db*"))
def sig(): 
    o=[]
    for f in WATCH:
        try: st=os.stat(f); o.append((f,st.st_mtime_ns,st.st_size))
        except OSError: pass
    return tuple(o)
if __name__=="__main__":
    # Event-driven: only look at the WeChat window when WeChat writes a new message to its local DB
    # (stat of encrypted DB files only; never read/decrypt). Heartbeat scan every 120s as fallback.
    # force_rescan: after mention_not_found (chat pane not loaded), rescan on next loop (~1s), not wait for db.
    a=sys.argv; sys.path.insert(0,"/workspace/wxauto/humanize"); import gate as _G; dry=("--dry" in a) or not _G.send_enabled();
    if dry and "--dry" not in a: print("SEND_ENABLED=false -> forced dry (只看不发)", flush=True)
    iv=float(a[a.index("--interval")+1]) if "--interval" in a else 1
    last=None; hb=0; force_rescan=False
    while True:
        s=sig()
        if s!=last or time.time()-hb>120 or force_rescan or "--once" in a:
            last=s; hb=time.time(); force_rescan=False; time.sleep(0.3); rc=once(dry)
            # Never exit on rc==3 (LLM/queue) or rc==4 (logged out): wait for manual
            # login so the watchdog/self-check always sees a live watcher.
            if "--once" in a: sys.exit(rc)
            if rc==4: jlog("mention_log.jsonl",{"event":"logged_out_waiting"})
            if rc==3: jlog("mention_log.jsonl",{"event":"rc3_ignored_continue"})
            if rc==2:
                force_rescan=True
                jlog("mention_log.jsonl",{"event":"force_rescan_armed"})
        time.sleep(iv)
