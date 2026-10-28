#!/usr/bin/env python3
"""Minimal client for local Kev (Jev/System One compatible) server.
Usage:
  python decide.py choice "<state>" "<instructions>" opt1 opt2 ...
  python decide.py noul "<state>" "<question>"
Env: KEV_URL (default http://127.0.0.1:8009)
"""
import json, os, sys, time, urllib.request
URL = os.environ.get("KEV_URL", "http://127.0.0.1:8009") + "/v1/systemone"

def ask(state, questions, timeout=120):
    body = json.dumps({"state": state, "model": "kev-latest", "questions": questions}).encode()
    req = urllib.request.Request(URL, body, {"content-type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    out["client_ms"] = round((time.perf_counter() - t) * 1000, 1)
    return out

def choice(state, instructions, options):
    crit = options if isinstance(options, dict) else {o: None for o in options}
    return ask(state, {"q": {"type": "choice", "instructions": instructions, "criteria": crit}})

def noul(state, question):
    return ask(state, {"q": {"type": "noul", "instructions": question}})

if __name__ == "__main__":
    kind, state, instr, *opts = sys.argv[1:]
    r = choice(state, instr, opts) if kind == "choice" else noul(state, instr)
    print(json.dumps(r, ensure_ascii=False, indent=1))
