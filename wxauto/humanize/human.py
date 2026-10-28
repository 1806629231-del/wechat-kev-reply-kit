"""拟人化输入：HumanCursor(SystemCursor/pyautogui) 弧线移动，失败退回 hsend.py 的贝塞尔+xdotool。
中文：分 2–4 段粘贴，段间停顿；思考时间随长度 2–8s；回车前随机停顿。"""
import random, time, subprocess as sp, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_hc=None
def _cursor():
    global _hc
    if _hc is None:
        try:
            from humancursor import SystemCursor; _hc=SystemCursor()
        except Exception: _hc=False
    return _hc
def move(x, y, jitter=4):
    x+=random.randint(-jitter,jitter); y+=random.randint(-jitter,jitter)
    c=_cursor()
    try:
        if c: c.move_to([x,y]); return "humancursor"
    except Exception: pass
    import hsend; hsend.move(x,y); return "bezier"
def click(x, y, jitter=4):
    how=move(x,y,jitter); time.sleep(random.uniform(.08,.3))
    sp.run(["xdotool","mousedown","1"]); time.sleep(random.uniform(.05,.14)); sp.run(["xdotool","mouseup","1"]); return how
def think(text, fast=False):
    t=min(4.0, 1.0+len(text)*0.04)*random.uniform(.8,1.15)
    if fast: t=0
    time.sleep(t); return round(t,2)
def chunks(text):
    n=1 if len(text)<8 else random.randint(2,min(4,max(2,len(text)//6)))
    cut=sorted(random.sample(range(1,len(text)),n-1)) if n>1 else []
    b=[0]+cut+[len(text)]; return [text[b[i]:b[i+1]] for i in range(len(b)-1)]
def paste_chunks(text, fast=False):
    parts=chunks(text)
    for p in parts:
        sp.run(["xclip","-selection","clipboard"],input=p.encode(),check=True)
        sp.run(["xdotool","key","ctrl+v"],check=True)
        time.sleep(0.05 if fast else random.uniform(.4,1.6))
    return len(parts)
def pre_enter(fast=False): time.sleep(0 if fast else random.uniform(.6,2.0))
