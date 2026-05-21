#!/usr/bin/env python3
"""Demonstrate Ollama prefix-cache behaviour over three turns.

turn1: cold full ingest
turn2: same prefix, grown conversation  -> should be a cache hit (fast)
turn3: same as turn2 but ONE token changed at the FRONT -> full re-ingest (slow)
"""
import json, time, urllib.request
HOST = "http://10.63.0.32:11434/api/chat"
MODEL = "qwen3.6:27b"
HEADER = "You are Hermes, an agentic assistant. Session started 2026-05-21T02:15:00Z."
BODY = ("\nTOOL: read_file(path) reads a file. TOOL: edit(path,old,new) edits. "
        "Follow the routing rules carefully and call tools precisely. ") * 900


def chat(messages, label):
    body = {"model": MODEL, "messages": messages, "stream": False,
            "options": {"num_predict": 8, "temperature": 0}}
    req = urllib.request.Request(HOST, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=600))
    pe = r.get("prompt_eval_count", 0)
    ped = r.get("prompt_eval_duration", 1) / 1e9
    print(f"[{label}] prompt_tokens={pe} ingest={ped:.2f}s -> {pe/ped:.0f} t/s")


sys1 = {"role": "system", "content": HEADER + BODY}
m = [sys1, {"role": "user", "content": "Task A: list two files."}]
chat(m, "turn1 cold")
m = m + [{"role": "assistant", "content": "OK, files: a.py, b.py."},
         {"role": "user", "content": "Task B: now read a.py."}]
chat(m, "turn2 grow, prefix STABLE")
sys2 = {"role": "system", "content": HEADER.replace("02:15:00", "02:31:00") + BODY}
chat([sys2] + m[1:], "turn3 grow, FRONT changed")
