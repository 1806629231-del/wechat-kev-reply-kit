"""Sticker tagging + meaning-matched selection (DeepSeek official API from .env)."""
import json, os, re, time, urllib.request, difflib
D=os.path.dirname(os.path.abspath(__file__))
CAT_PATH=f"{D}/stickers/catalog.json"
INTENTS=["agree","laugh","refuse","apologize","sad","angry","teasing","thanks","greet","cute","surprise","confused","tired","wait","praise","speechless","goodnight"]
HOSTILE_RE=re.compile(r"讨厌你|滚|去死|闭嘴|傻[逼b]|妈的|顶你个肺|弄你|电死你|暴打|邪恶|这么猪|已愤怒")

def _env():
    e={}
    for l in open(f"{D}/.env",encoding="utf-8"):
        l=l.strip()
        if l and not l.startswith("#") and "=" in l:
            k,v=l.split("=",1); e[k.strip()]=v.strip().strip('"').strip("'")
    return e
_E=_env()

def llm(messages, max_tokens=200, temperature=0.2, timeout=20):
    body=json.dumps({"model":_E.get("LLM_MODEL","deepseek-chat"),"messages":messages,
                     "max_tokens":max_tokens,"temperature":temperature}).encode()
    req=urllib.request.Request(_E["LLM_BASE_URL"].rstrip("/")+"/chat/completions",data=body,
        headers={"Content-Type":"application/json","Authorization":"Bearer "+_E["LLM_API_KEY"]})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()

def load(): return json.load(open(CAT_PATH,encoding="utf-8"))

def is_hostile(c):
    return c.get("polarity")=="negative" or c.get("intent") in ("angry","refuse") \
        or bool(HOSTILE_RE.search((c.get("caption","")+c.get("desc",""))))

# ---------- tagging ----------
def tag_catalog(force=False):
    from rapidocr_onnxruntime import RapidOCR
    from PIL import Image
    ocr=RapidOCR(); cat=load(); changed=0
    for c in cat:
        if not force and all(k in c for k in ("caption","intent","polarity")): continue
        cap=""
        try:
            p=c["path"]
            if p.endswith(".gif"):
                im=Image.open(p).convert("RGB"); p2="/tmp/_stk.png"; im.save(p2); p=p2
            res,_=ocr(p)
            cap=" ".join(r[1] for r in (res or []) if r[2]>0.5)
        except Exception as e: cap=""
        q=(f"表情包描述：{c.get('desc','')}\n图上OCR文字：{cap or '(无)'}\n"
           f"返回JSON：{{\"caption\":图上实际配字(没有则空串，修正OCR错字),\"intent\":从{INTENTS}选一个,"
           "\"polarity\":positive/neutral/negative(对收到者是友好、中性还是敌意/冒犯/负面)}。只输出JSON。")
        out=llm([{"role":"user","content":q}],max_tokens=120,temperature=0)
        m=re.search(r"\{.*\}",out,re.S); j=json.loads(m.group(0))
        c["caption"]=j.get("caption",cap) or ""; c["intent"]=j.get("intent","cute")
        c["polarity"]=j.get("polarity","neutral"); c["ocr_raw"]=cap
        if HOSTILE_RE.search(c["caption"]+c.get("desc","")): c["polarity"]="negative"
        changed+=1
    tmp=CAT_PATH+".tmp"; json.dump(cat,open(tmp,"w",encoding="utf-8"),ensure_ascii=False,indent=1); os.replace(tmp,CAT_PATH)
    return changed

# ---------- selection ----------
def _sim(a,b):
    a=a or ""; b=b or ""
    ca=set(a); cb=set(b)
    return len(ca&cb)/max(1,len(cb)) *0.5 + difflib.SequenceMatcher(None,a,b).ratio()*0.5

def candidates(reply, context="", k=8, cat=None, allow_negative=False):
    cat=cat or load()
    pool=[c for c in cat if allow_negative or not is_hostile(c)]
    t=(reply or "")+" "+(context or "")
    scored=[]
    for c in pool:
        s=_sim(reply, c.get("caption","")+c.get("desc",""))
        if "{STICKER_TOPIC}" in c.get("desc","") or "DeepSeek" in c.get("desc",""): s+=0.05  # preferred
        if re.search(r"deepseek|{STICKER_TOPIC}|{STICKER_TOPIC}",t,re.I) and "{STICKER_TOPIC}" in c.get("desc",""): s+=0.2
        scored.append((s,c))
    scored.sort(key=lambda x:-x[0])
    # ensure intent diversity
    out=[]; seen=set()
    for s,c in scored:
        if c.get("intent") in seen and len(out)<k//2: continue
        out.append(c); seen.add(c.get("intent"))
        if len(out)>=k: break
    return out

def pick(reply, context="", k=8, forced=False):
    """Return (sticker_dict|None, info)."""
    cat=load()
    # tone of reply: polite/friendly replies never get negative stickers
    cands=candidates(reply,context,k=k,cat=cat)
    lines="\n".join(f'{c["id"]}: 配字「{c.get("caption","")}」 情绪={c.get("intent")} 极性={c.get("polarity")} 画面={c.get("desc","")[:40]}' for c in cands)
    q=("你在给微信群聊回复配表情包。表情包的配字和情绪必须与回复的意思和语气一致；"
       "不能用拒绝/讨厌/生气/骂人类表情配客气或友好的回复；意思不搭就返回NONE。"
       "合适时尽量配一个（同等合适优先DeepSeek{STICKER_TOPIC}）。"
       + ("对方明确要表情包，尽量选一个最贴题的。" if forced else "")+
       f"\n对方消息：{context}\n我的回复：{reply}\n候选：\n{lines}\n只输出一个候选id或NONE。")
    t0=time.perf_counter()
    try: out=llm([{"role":"user","content":q}],max_tokens=10,temperature=0)
    except Exception as e: return None,{"err":str(e)[-120:]}
    ms=round((time.perf_counter()-t0)*1000)
    m=re.search(r"s\d+",out); byid={c["id"]:c for c in cands}
    c=byid.get(m.group(0)) if m else None
    if c and is_hostile(c): c=None   # hard guard
    return c,{"llm_out":out,"pick_ms":ms,"cands":[x["id"] for x in cands]}

if __name__=="__main__":
    import sys
    if "--tag" in sys.argv: print("tagged",tag_catalog(force="--force" in sys.argv))
