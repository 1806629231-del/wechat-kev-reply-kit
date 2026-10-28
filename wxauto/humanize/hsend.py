#!/usr/bin/env python3
"""原型：拟人化鼠标/打字（xdotool，贝塞尔轨迹+随机延迟）。未对微信运行过。
用法: hsend.py move X Y | click X Y | type "文本"  (中文走剪贴板粘贴+停顿)"""
import random, subprocess, sys, time, math
def xd(*a): subprocess.run(["xdotool", *map(str, a)], check=True)
def pos():
    o = subprocess.check_output(["xdotool","getmouselocation","--shell"]).decode()
    d = dict(l.split("=") for l in o.split()); return int(d["X"]), int(d["Y"])
def move(x, y):
    x0, y0 = pos(); dist = math.hypot(x-x0, y-y0) or 1
    c1 = (x0+(x-x0)*random.uniform(.2,.4)+random.gauss(0,dist*.1), y0+(y-y0)*random.uniform(.2,.4)+random.gauss(0,dist*.1))
    c2 = (x0+(x-x0)*random.uniform(.6,.8)+random.gauss(0,dist*.1), y0+(y-y0)*random.uniform(.6,.8)+random.gauss(0,dist*.1))
    n = max(15, int(dist/12))
    for i in range(1, n+1):
        t = i/n; t = t*t*(3-2*t)  # ease-in-out
        bx = (1-t)**3*x0+3*(1-t)**2*t*c1[0]+3*(1-t)*t*t*c2[0]+t**3*x
        by = (1-t)**3*y0+3*(1-t)**2*t*c1[1]+3*(1-t)*t*t*c2[1]+t**3*y
        xd("mousemove", int(bx), int(by)); time.sleep(random.uniform(.004,.015))
    xd("mousemove", x+random.randint(-2,2), y+random.randint(-2,2))
def click(x, y):
    move(x, y); time.sleep(random.uniform(.08,.3))
    xd("mousedown",1); time.sleep(random.uniform(.05,.14)); xd("mouseup",1)
def type_(s):
    for ch in s:
        if ord(ch) < 128: xd("type","--delay",0,ch)
        else:
            subprocess.run(["xclip","-sel","clip"], input=ch.encode()); xd("key","ctrl+v")
        time.sleep(max(.03, random.gauss(.16,.07)) + (random.uniform(.4,1.2) if ch in "，。！？ " else 0))
if __name__ == "__main__":
    c = sys.argv[1]
    {"move": lambda: move(int(sys.argv[2]), int(sys.argv[3])),
     "click": lambda: click(int(sys.argv[2]), int(sys.argv[3])),
     "type": lambda: type_(sys.argv[2])}[c]()
