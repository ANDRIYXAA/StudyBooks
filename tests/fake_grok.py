#!/usr/bin/env python3
"""Stand-in for the `grok` CLI (Grok Build) in offline tests: `login --device-auth` and a headless run with --prompt-file.
FAKE_GROK = ok | limit | auth | fail | nofile ; FAKE_GROK_LOG gets the prompt and the arguments."""
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
home = Path(os.environ.get("HOME", Path.home())) / ".grok"

if args[:2] == ["login", "--device-auth"]:
    print("To sign in, open this URL in your browser:\n", flush=True)
    print("  https://accounts.x.ai/oauth2/device?user_code=AB12-CD34\n", flush=True)
    print("Confirm this code in your browser:\n", flush=True)
    print("  AB12-CD34\n", flush=True)
    print("Waiting for authorization...", flush=True)
    time.sleep(float(os.environ.get("FAKE_GROK_LOGIN_SECONDS", "1.5")))
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text('{"fake": true}')
    sys.exit(0)
if args[:1] == ["logout"]:
    (home / "auth.json").unlink(missing_ok=True)
    sys.exit(0)
if "--prompt-file" in args:
    prompt = Path(args[args.index("--prompt-file") + 1]).read_text(encoding="utf-8")
    if os.environ.get("FAKE_GROK_LOG"):
        with open(os.environ["FAKE_GROK_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"args": args, "prompt": prompt, "home": os.environ.get("HOME"), "env_key": os.environ.get("XAI_API_KEY")}) + "\n")
    mode = os.environ.get("FAKE_GROK", "ok")
    cwd = Path(args[args.index("--cwd") + 1]) if "--cwd" in args else Path.cwd()
    if mode == "limit":
        print(json.dumps({"type": "error", "message": "429 Too Many Requests: rate limit exceeded. Try again in 2 hours."}))
        sys.exit(1)
    if mode == "auth":
        print(json.dumps({"type": "error", "message": "Not signed in. To authenticate without a browser, run: grok login --device-code"}))
        sys.exit(1)
    if mode == "fail":
        print(json.dumps({"type": "error", "message": "the tool crashed"}))
        sys.exit(1)
    if mode != "nofile":
        (cwd / "grok_out.txt").write_text("written by grok\n", encoding="utf-8")
    print(json.dumps({"type": "result", "result": "Done by grok."}))
    sys.exit(0)
sys.exit(2)
