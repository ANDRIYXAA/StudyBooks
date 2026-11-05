#!/usr/bin/env python3
"""Stand-in for the `codex` CLI in offline tests: `login status`, `login --device-auth`, `logout`, `exec --json`.
Behaviour is picked by env: FAKE_CODEX = ok | limit | auth | fail | nofile; FAKE_CODEX_LOG = a file that gets the stdin prompt."""
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
home.mkdir(parents=True, exist_ok=True)
auth = home / "auth.json"

if args[:2] == ["login", "status"]:
    if auth.exists():
        print("Logged in using ChatGPT")
        sys.exit(0)
    print("Not logged in")
    sys.exit(1)
if args[:2] == ["login", "--device-auth"]:
    print("Open this link in your browser and sign in:", flush=True)
    print("  https://auth.example.test/codex/device", flush=True)
    print("Enter this one-time code: AB12-CD34", flush=True)
    time.sleep(float(os.environ.get("FAKE_CODEX_LOGIN_SECONDS", "1.5")))
    auth.write_text('{"fake": true}')
    sys.exit(0)
if args[:1] == ["logout"]:
    auth.unlink(missing_ok=True)
    sys.exit(0)
if args[:1] == ["exec"]:
    prompt = sys.stdin.read()
    if os.environ.get("FAKE_CODEX_LOG"):
        with open(os.environ["FAKE_CODEX_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"args": args, "prompt": prompt, "home": str(home)}) + "\n")
    mode = os.environ.get("FAKE_CODEX", "ok")
    cwd = Path(args[args.index("-C") + 1]) if "-C" in args else Path.cwd()
    print(json.dumps({"type": "thread.started", "thread_id": "t1"}), flush=True)
    if mode == "limit":
        print(json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again in 3 hours."}}), flush=True)
        sys.exit(1)
    if mode == "auth":
        print(json.dumps({"type": "error", "message": "401 Unauthorized: please sign in again"}), flush=True)
        sys.exit(1)
    if mode == "fail":
        print(json.dumps({"type": "turn.failed", "error": {"message": "the sandbox crashed"}}), flush=True)
        sys.exit(1)
    if mode != "nofile":
        (cwd / "codex_out.txt").write_text("written by codex\n", encoding="utf-8")
    print(json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Done by codex."}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 0}}), flush=True)
    sys.exit(0)
sys.exit(2)
