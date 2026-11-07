#!/usr/bin/env python3
"""Stand-in for the `agy` (Antigravity CLI) in offline tests.
 - with -p on a terminal and no token in $HOME/.gemini/antigravity-cli: the Google sign-in: prints a link, waits for the code on
   stdin, stores a token file and answers "OK" (the real CLI does the same, and waits 60 s at most)
 - with -p and no terminal: a headless run; the task is in the file _agy_task.md of the current directory.
   FAKE_GEMINI = ok | limit | auth | fail | nofile ; FAKE_GEMINI_LOG gets the task and the arguments."""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
home = Path(os.environ.get("HOME", Path.home()))
tokdir = home / ".gemini" / "antigravity-cli"
tokdir.mkdir(parents=True, exist_ok=True)

if "-p" in args and sys.stdin.isatty():
    print("Authentication required. Please visit the URL to log in:\n  https://accounts.google.com/o/oauth2/auth?client_id=fake&state=abc\n", flush=True)
    print("Waiting for authentication (timeout 60s)...\nOr, paste the authorization code here and press Enter:", flush=True)
    code = sys.stdin.readline().strip()
    if code == "GOODCODE":
        (tokdir / "antigravity-oauth-token").write_text('{"fake": true}')
        print("OK", flush=True)
        sys.exit(0)
    print("Error: authentication failed", flush=True)
    sys.exit(1)

task_file = Path("_agy_task.md")
prompt = task_file.read_text(encoding="utf-8") if task_file.exists() else ""
if os.environ.get("FAKE_GEMINI_LOG"):
    with open(os.environ["FAKE_GEMINI_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "prompt": prompt, "home": str(home), "cwd": os.getcwd(), "env_key": os.environ.get("GEMINI_API_KEY")}) + "\n")
mode = os.environ.get("FAKE_GEMINI", "ok")


def error(msg):
    print(json.dumps({"conversation_id": "", "status": "ERROR", "response": "", "error": msg, "finished_reason": "error"}))
    sys.exit(1)


if mode == "limit":
    error("[API Error: 429 Quota exceeded for quota metric 'Gemini requests'. Please retry in 3 hours.]")
if mode == "auth":
    error("authentication failed or timed out")
if mode == "fail":
    error("the tool crashed")
if mode != "nofile":
    Path("gemini_out.txt").write_text("written by gemini\n", encoding="utf-8")
print(json.dumps({"conversation_id": "c1", "status": "SUCCESS", "response": "Done by gemini.", "error": "", "num_turns": 2}))
