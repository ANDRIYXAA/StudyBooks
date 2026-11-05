#!/usr/bin/env python3
"""Stand-in for the `gemini` CLI in offline tests.
 - on a terminal and without -p: the Google sign-in: prints a link, reads the code, stores GEMINI_CLI_HOME/.gemini/oauth_creds.json
 - with -p: a headless run; the prompt is on stdin. FAKE_GEMINI = ok | limit | auth | fail | nofile ; FAKE_GEMINI_LOG gets the prompt."""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
home = Path(os.environ.get("GEMINI_CLI_HOME", Path.home())) / ".gemini"
home.mkdir(parents=True, exist_ok=True)

if "-p" not in args and sys.stdin.isatty():
    print("Please visit the following URL to authorize the application:\n", flush=True)
    print("https://accounts.google.com/o/oauth2/v2/auth?client_id=fake&state=abc\n", flush=True)
    print("Enter the authorization code: ", end="", flush=True)
    code = sys.stdin.readline().strip()
    if code == "GOODCODE":
        (home / "oauth_creds.json").write_text('{"fake": true}')
        print("\nAuthenticated.", flush=True)
        import time
        time.sleep(30)                     # the real CLI goes on to its main screen: the app must end it
        sys.exit(0)
    print("\nInvalid code.", flush=True)
    sys.exit(1)

prompt = sys.stdin.read()
if os.environ.get("FAKE_GEMINI_LOG"):
    with open(os.environ["FAKE_GEMINI_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "prompt": prompt, "home": str(home.parent), "cwd": os.getcwd(), "env_key": os.environ.get("GEMINI_API_KEY")}) + "\n")
mode = os.environ.get("FAKE_GEMINI", "ok")
if mode == "limit":
    print("[API Error: 429 Quota exceeded for quota metric 'Gemini requests'. Please retry in 3 hours.]", file=sys.stderr)
    sys.exit(1)
if mode == "auth":
    print("Manual authorization is required but the current session is non-interactive.", file=sys.stderr)
    sys.exit(41)
if mode == "fail":
    print("the tool crashed", file=sys.stderr)
    sys.exit(1)
if mode != "nofile":
    Path("gemini_out.txt").write_text("written by gemini\n", encoding="utf-8")
print(json.dumps({"response": "Done by gemini.", "stats": {"models": {}}}))
