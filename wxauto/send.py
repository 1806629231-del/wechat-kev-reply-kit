#!/usr/bin/env python3
"""send.py <chat name> <message> [--dry]  (DISPLAY :2). Dry = everything except paste+Enter.
Also importable: send(chat,msg,dry=False,eng=None,already_open=False,before=None,image=None,fast=False) -> (ok, log)."""
import os, sys, time, re, subprocess as sp
sys.path.insert(0,'/workspace/wxauto/humanize'); import human as HI, gate as GATE
os.environ.setdefault("DISPLAY", ":2")
sys.path.insert(0, "/workspace/kev"); import decide
import mss, numpy as np
N=lambda z:z.replace(" ","").lower()
def _nm(z):
    """Normalize chat name: keep CJK/letters/digits only (drops emoji, symbols, spaces, ，。.!！~～ …)."""
    return re.sub(r"[^\u4e00-\u9fff\u3040-\u30ffA-Za-z0-9]","",z or "").lower()
def names_match(a,b):
    """Normalized exact, truncated-prefix, or substring match (min 4 chars to avoid OCR noise)."""
    na,nb=_nm(a),_nm(b)
    if not na or not nb: return False
    if na==nb: return True
    if min(len(na),len(nb))>=4 and (na.startswith(nb) or nb.startswith(na)): return True
    return False
DLG={"ok","cancel","确定","取消","logout","logout?"}
_eng=None
def get_eng():
    global _eng
    if _eng is None:
        from rapidocr_onnxruntime import RapidOCR; _eng=RapidOCR()
    return _eng
def x(*a): sp.run(["xdotool",*a],check=True)
def clip(t): sp.run(["xclip","-selection","clipboard"],input=t.encode(),check=True)
class Abort(Exception): pass
def clip_image(path):
    import PIL.Image as I, io
    b=io.BytesIO(); I.open(path).convert("RGBA").save(b,"PNG")
    sp.run(["xclip","-selection","clipboard","-t","image/png","-i"],input=b.getvalue(),check=True)
def grab_pane(l=400,t=200,w=500,h=330):
    wid=sp.check_output(["xdotool","search","--name","^Weixin$"]).split()[0].decode()
    g=dict(z.split("=") for z in sp.check_output(["xdotool","getwindowgeometry","--shell",wid]).decode().split())
    with mss.MSS() as s: return np.array(s.grab({"left":int(g["X"])+l,"top":int(g["Y"])+t,"width":w,"height":h}))[:,:,:3].astype(int)
def send_image(chat,path,dry=False,eng=None,already_open=True,fast=False):
    """Paste image via xclip clipboard + Ctrl+V into input, same header/Kev/dialog checks; verify by chat-pane pixel change.
    fast=True (after text already sent in same chat): skip kev confirm + header OCR + second dialog OCR."""
    return send(chat,None,dry=dry,eng=eng,already_open=already_open,before=0,image=path,fast=fast)
def _unreadable(name):
    """True if the chat name has too few OCR-able chars (e.g. emoji-only like ❤️❤️❤️)."""
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", name or ""))<2
LIST_TOP, ROW_H, LIST_X = 85, 67.7, 190
def _wf_rows(chat_user):
    """(index in weflow session order, session, recent text snippets) for chat_user."""
    sys.path.insert(0, "/workspace/wxauto"); import weflow_read as WF
    ss=WF.sessions(limit=30)
    ss=sorted(ss, key=lambda z: -(z.get("sortTimestamp") or z.get("lastTimestamp") or 0))
    idx=next((i for i,z in enumerate(ss) if z.get("username")==chat_user), None)
    texts=[]
    try:
        for m in WF.messages(chat_user, n=8):
            t=(m.get("parsedContent") or m.get("content") or "").strip()
            if len(re.sub(r"\[[^\]]*\]","",t).strip())>=3 and not t.startswith("[") : texts.append(t)
    except Exception: pass
    return idx, (ss[idx] if idx is not None else None), ss, texts
def send(chat,msg,dry=False,eng=None,already_open=False,before=None,image=None,fast=False,mention=None,mentions=None,chat_user=None):
    """mention=<group display name>: msg starting with '@name ' gets a real WeChat mention chip via the @ member picker (falls back to plain text)."""
    eng=eng or get_eng(); T0=time.perf_counter(); log=[]
    def mark(s): log.append((s, round((time.perf_counter()-T0)*1000)))
    def stop(m): raise Abort(f"{m} {log}")
    wid=sp.check_output(["xdotool","search","--name","^Weixin$"]).split()[0].decode()
    x("windowactivate","--sync",wid)
    g=dict(l.split("=") for l in sp.check_output(["xdotool","getwindowgeometry","--shell",wid]).decode().split()); X,Y=int(g["X"]),int(g["Y"])
    mark("activate")
    def ocr(l,t,w,h,pad=0):
        with mss.MSS() as s: im=np.array(s.grab({"left":X+l,"top":Y+t,"width":w,"height":h}))[:,:,:3]
        if pad: im=np.pad(im,((pad,pad),(pad,pad),(0,0)),constant_values=255)
        r,_=eng(np.ascontiguousarray(im)); return r or []
    def dialog():
        # dialogs land in the center; smaller crop is enough and much faster
        t=[N(r[1]) for r in ocr(200,150,500,350)]
        return [w for w in t if w in DLG or "logout" in w or "loggingout" in w]
    def dialog_cheap():
        # smaller center crop — one cheap check for fast path
        t=[N(r[1]) for r in ocr(300,220,300,200)]
        return [w for w in t if w in DLG or "logout" in w or "loggingout" in w]
    def count_bubbles():
        # only the lower chat pane — new bubbles appear here
        return sum(1 for r in ocr(400,320,500,200) if N(r[1])==N(msg) or (len(N(r[1]))>=6 and N(r[1]) in N(msg)))
    def header():
        return re.sub(r"[（(]\d+[)）]\s*$",""," ".join(r[1] for r in ocr(314,30,400,60,20)).strip())
    def open_by_user():
        """Open chat by weflow session position (time label cross-check), verify via weflow message text in pane."""
        idx,sess,ss,texts=_wf_rows(chat_user)
        if idx is None: stop(f"weflow: {chat_user} not in sessions")
        if dialog(): stop("dialog visible at start")
        rows=ocr(115,LIST_TOP,200,int(ROW_H*8))
        # cross-check: OCR'd names of OTHER sessions tell us the real on-screen row of each session
        def row_of(y): return int(y//ROW_H)
        known={}
        for b,t,_ in rows:
            for j,z in enumerate(ss):
                if z.get("username")!=chat_user and names_match(t, z.get("displayName") or ""):
                    known[j]=row_of(sum(p[1] for p in b)/4)
        # shift = on-screen row - weflow index, from sessions ranked just before/after target
        cand=idx
        near=[(abs(j-idx),r-j) for j,r in known.items()]
        if near: cand=idx+sorted(near)[0][1]
        # time-label check on candidate row (HH:MM of last message)
        lt=time.strftime("%H:%M", time.localtime(sess.get("lastTimestamp") or sess.get("sortTimestamp") or 0))
        rowtxt=[t for b,t,_ in rows if row_of(sum(p[1] for p in b)/4)==cand]
        mark(f"weflow idx={idx} row={cand} time={lt} rowtxt={rowtxt[:3]}")
        if cand<0 or cand>7: stop(f"row {cand} not visible")
        if rowtxt and not any(lt in N(t) for t in rowtxt) and any(names_match(t, z.get("displayName") or "") for t in rowtxt for z in ss if z.get("username")!=chat_user):
            stop(f"row {cand} belongs to another chat {rowtxt}")
        x("mousemove","--sync",str(X+LIST_X),str(int(Y+LIST_TOP+ROW_H*cand+ROW_H/2)),"click","1"); time.sleep(0.5); mark("click row (weflow pos)")
        P=lambda z: re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]","",z or "").lower()
        pane="".join(P(r[1]) for r in ocr(320,60,580,450))
        okv=[t for t in texts if len(P(t))>=3 and (P(t)[:6] in pane)]
        mark(f"verify pane via weflow texts hit={len(okv)}/{len(texts)}")
        if texts and not okv: stop("opened chat does not match weflow messages")
        if not texts:
            # no text history to compare: require the header to NOT be another known chat
            title=header()
            if any(names_match(title, z.get("displayName") or "") for z in ss if z.get("username")!=chat_user): stop(f"header is other chat {title!r}")
    opened_by_user=False
    if chat_user and not already_open and _unreadable(chat):
        open_by_user(); opened_by_user=True; before=None
    # Fast path: already in chat after a successful text send — skip OCR header + kev confirm
    if opened_by_user:
        mark("opened via weflow position")
    elif already_open and fast:
        mark("fast skip header+kev")
    else:
        title=header() if already_open else ""
        if not names_match(title, chat):  # not already in the target chat -> search list (with Kev)
            if dialog(): stop("dialog visible at start")
            mark("dialog check 1")
            rows=[(r[1],r[0]) for r in ocr(115,85,150,500)]; mark("ocr chat list")
            names=[t for t,_ in rows]
            k=decide.choice("WeChat chat list visible rows: "+" | ".join(names), f"Which row should be clicked to open the chat named '{chat}'?", names+["none_of_these"])
            mark("kev choose row"); pick=k["answers"]["q"]["choice"]
            prev=lambda t: (":" in t or "：" in t)  # message preview lines, not chat names
            hit=[b for t,b in rows if not prev(t) and names_match(t, chat)]
            hit=[b for t,b in rows if not prev(t) and _nm(t)==_nm(chat)] or hit
            searched=False
            if not names_match(pick, chat) or not hit or len(hit)>1:
                # not confidently visible -> WeChat search box (never Escape)
                x("key","ctrl+f"); time.sleep(0.4); clip(chat); x("key","ctrl+a"); x("key","ctrl+v"); time.sleep(1.2)
                x("key","Return"); time.sleep(0.8); searched=True; mark("search open")
                title=header(); before=None
                if not names_match(title, chat):
                    if chat_user:
                        open_by_user(); opened_by_user=True
                    else: stop(f"search header {title!r} != {chat!r} (rows {names})")
            if not opened_by_user and not searched:
                b=hit[0]; x("mousemove","--sync",str(int(X+115+sum(p[0] for p in b)/4)),str(int(Y+85+sum(p[1] for p in b)/4)),"click","1"); time.sleep(0.3); mark("click row")
                title=header()
                if not title: x("click","1"); time.sleep(0.3); title=header()
                before=None
        if not opened_by_user:
            mark("ocr header")
            k2=decide.noul(f"Open chat title: '{title}'. Target chat: '{chat}'.","Is the open chat title the same as the target chat (allow truncated/prefix names)?"); mark("kev confirm")
            if not names_match(title, chat) or k2["answers"]["q"]["noul"]<0.7: stop(f"header {title!r} noul {k2['answers']['q']['noul']}")
    if image is None and before is None: before=count_bubbles(); mark(f"count before={before}")
    x("mousemove","--sync",str(X+610),str(Y+565),"click","1"); time.sleep(0.08 if fast else 0.12); mark("click input")
    if fast:
        d=dialog_cheap()
        if d: stop(f"dialog before paste {d}")
        mark("dialog cheap (pre-paste)")
    else:
        d=dialog()
        if d: stop(f"dialog before paste {d}")
        mark("dialog check (pre-paste)")
    def insert_mention(name):
        """Type real '@', paste name prefix, OCR member picker, click matching entry. True if chip inserted."""
        pre=name.strip()[:4]
        x("mousemove","--sync",str(X+800),str(Y+590),"click","1"); time.sleep(0.05); x("key","End")
        x("type","@"); time.sleep(0.25)
        clip(pre); x("key","ctrl+v")
        for i in range(6):
            time.sleep(0.25)
            rows=ocr(260,290,460,240)  # picker pops up above input, near caret
            hit=[r for r in rows if names_match(r[1],name) or N(r[1])==N(name)]
            if not hit:  # OCR may drop chars; accept unique row containing prefix
                c=[r for r in rows if N(pre) in N(r[1]) and not N(r[1]).startswith("@")]
                hit=c if len(c)==1 else []
            if hit:
                b=hit[0][0]
                x("mousemove","--sync",str(int(X+260+sum(p[0] for p in b)/4)),str(int(Y+290+sum(p[1] for p in b)/4)),"click","1")
                time.sleep(0.25); mark(f"picker click {hit[0][1]!r}"); return True
        # no popup: remove typed '@'+prefix so plain text can be pasted cleanly
        # NOTE: never press Escape here — in WeChat 4.1 Linux it opens the "Log out?" dialog
        for _ in range(len(pre)+1): x("key","BackSpace")
        return False
    if dry: mark("DRY stop"); return True, log
    if image:
        pane0=grab_pane(); clip_image(image); x("key","ctrl+v"); time.sleep(0.35 if fast else 0.5); mark("paste image")
    else:
        # leading '@name ' tokens -> real chips (needs mention hint so we know it's a group reply)
        lead=[]; rest=msg
        if mention or mentions:
            while True:
                m=re.match(r"@(\S+?)\s+",rest)
                if not m or len(lead)>=5: break
                lead.append(m.group(1)); rest=rest[m.end():]
        rest=rest.replace("@","\uff20")  # in-body '@' must never open the picker
        def input_text():
            return N("".join(r[1] for r in ocr(330,505,580,110)))
        def picker_open():
            return len(ocr(260,290,460,200))>=3 and N(input_text()).endswith("@")
        def compose(real):
            chips=0
            for nm in lead:
                if real and insert_mention(nm): chips+=1; mark(f"chip {nm}")
                else: clip("\uff20"+nm+" "); x("key","ctrl+v"); time.sleep(0.05); mark(f"plain {nm}")
            # close any stray picker by clicking the text area (never Escape)
            mark("move:"+HI.click(X+800,Y+590)); time.sleep(0.1)
            mark(f"think {HI.think(rest, fast=dry)}s"); mark(f"paste msg chunks={HI.paste_chunks(rest, fast=dry)}")
            return chips
        def ok_text():
            # fuzzy: non-empty, no dangling '@'/picker, and any 4+ char chunk of the message seen
            t=input_text()
            if not t: return False
            if t.rstrip().endswith("@") or t.rstrip().endswith("\uff20"): return False
            core=re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+"," ",rest.lower())
            tt=re.sub(r"[^0-9a-z\u4e00-\u9fff]+","",t.lower())
            words=[w for w in core.split() if len(w)>=4]
            chunks=[]
            for w in words: chunks+=[w[i:i+4] for i in range(0,len(w)-3,2)]
            if not chunks: return True  # nothing distinctive -> OCR unsure, allow
            return any(c in tt for c in chunks) or len(tt)>=4
        def clear():
            x("mousemove","--sync",str(X+610),str(Y+565),"click","1"); time.sleep(0.05)
            x("key","ctrl+a"); x("key","BackSpace"); time.sleep(0.1)
        chips=compose(True)
        if not ok_text():
            mark("verify text fail -> retry"); clear(); chips=compose(True)
            if not ok_text():
                mark("verify text fail -> plain fallback"); clear(); chips=compose(False)
                if not ok_text() and not input_text(): stop("input box empty after compose (not sent)")
                mark("verify unsure -> sending anyway")
        mark(f"text verified chips={chips}/{len(lead)}")
    if not fast:
        d=dialog()
        if d: stop(f"dialog before Enter {d} (message left in input box, not sent)")
        mark("dialog check (pre-Enter)")
    if os.environ.get("SEND_NO_ENTER")=="1": mark("NO_ENTER draft left in input"); return True, log
    HI.pre_enter(); x("key","Return"); mark("SEND keypress")
    if image:
        ch=0
        loops,step=(4,0.15) if fast else (8,0.25)
        for i in range(loops):
            time.sleep(step); ch=float((abs(grab_pane()-pane0).sum(axis=2)>30).mean())
            if ch>0.03: break
        mark(f"verify img change={ch:.3f}"); return ch>0.03, log
    after=before
    for i in range(6):
        time.sleep(0.12); after=count_bubbles()
        if after>before: break
    mark(f"verify after={after}")
    return after>before, log
# ---- cross-process send lock (mention_watch + task_worker + send_voice share the screen) ----
import fcntl as _fcntl, functools as _ft
_SEND_LOCK = "/tmp/wx_send.lock"
_lock_depth = 0
def _locked(fn):
    @_ft.wraps(fn)
    def w(*a, **k):
        global _lock_depth
        if _lock_depth:                      # re-entrant within one process
            return fn(*a, **k)
        with open(_SEND_LOCK, "w") as lk:
            _fcntl.flock(lk, _fcntl.LOCK_EX)
            _lock_depth += 1
            try: return fn(*a, **k)
            finally: _lock_depth -= 1
    return w
def _gated(fn, kind):
    """全局闸：SEND_ENABLED、限速、静默时段、长度、kill 开关。被拦时不碰微信，返回 (False, reason)。"""
    @_ft.wraps(fn)
    def w(chat, payload, *a, **k):
        dry = k.get("dry", a[0] if a else False)
        is_group = bool(k.get("mention") or k.get("mentions")) or (kind=="text" and k.get("chat_user") is None and k.get("_group", True) and not k.get("fast"))
        k.pop("_group", None)
        if kind=="text":
            payload = GATE.trim(payload)
        ok, why = GATE.check(chat, payload if kind=="text" else "[image]", is_group=is_group)
        if not dry and not GATE.send_enabled(): return False, ["GATE: SEND_ENABLED=false (humanize/config.json)"]
        if not ok:
            return (False, [f"GATE blocked: {why}"]) if not dry else (False, [f"GATE would block: {why}"])
        if not dry and (k.get("mention") or k.get("mentions")): GATE.mention_jitter()
        r = fn(chat, payload, *a, **k)
        if not dry and r and r[0]: GATE.record(chat)
        return r
    return w
send = _locked(_gated(send, "text"))
send_image = _locked(_gated(send_image, "image"))

if __name__=="__main__":
    args=[a for a in sys.argv[1:] if a not in("--dry","--image","--fast") and not a.startswith("--mention=")]; mn=next((a[10:] for a in sys.argv if a.startswith("--mention=")),None); dry="--dry" in sys.argv; fast="--fast" in sys.argv
    try: ok,log=(send_image(args[0],args[1],dry,already_open=False,fast=fast) if "--image" in sys.argv else send(args[0],args[1],dry,fast=fast,mention=mn))
    except Abort as e: print("ABORT:",e); sys.exit(2)
    print(("DRY OK" if dry else "VERIFIED" if ok else "NOT VERIFIED"), log)

