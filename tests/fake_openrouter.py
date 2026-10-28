"""A stand-in for the OpenRouter API for offline tests. `start()` runs it in a thread and returns (server, requests).

Behaviour is picked by the API key (errors) and by the model id / prompt (which scripted tool calls come back).
Pretends to be a model that follows the tasks: one step per round trip, decided by how many tool results it has seen."""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = [
    {"id": "fake/vision-tools", "name": "Fake Vision", "context_length": 200000,
     "architecture": {"input_modalities": ["text", "image"]}, "pricing": {"prompt": "0.000003", "completion": "0.000015"},
     "supported_parameters": ["tools", "tool_choice", "max_tokens"]},
    {"id": "fake/text-tools", "name": "Fake Text", "context_length": 64000,
     "architecture": {"input_modalities": ["text"]}, "pricing": {"prompt": "0.0000002", "completion": "0.000001"},
     "supported_parameters": ["tools", "tool_choice"]},
    {"id": "fake/no-tools", "name": "Fake No Tools", "context_length": 8000,
     "architecture": {"input_modalities": ["text"]}, "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": ["max_tokens"]},
]


def free(mid, name, vision, ii=None, agentic=None, created=1790000000, ctx=262144, expires=None):
    return {"id": mid, "name": name, "context_length": ctx, "created": created, "expiration_date": expires,
            "architecture": {"input_modalities": ["text", "image"] if vision else ["text"]},
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["tools", "tool_choice"],
            "benchmarks": None if ii is None else {"artificial_analysis": {"intelligence_index": ii, "agentic_index": agentic}}}


MODELS += [
    free("free/alpha:free", "Alpha", True, ii=40, agentic=20),            # score 50, sees images
    free("free/beta:free", "Beta", True, ii=30),                           # score 30, sees images
    free("free/gamma:free", "Gamma", False, ii=20, agentic=10),           # score 25
    free("free/delta-70b:free", "Delta 70B", False),                      # no benchmark: estimated from 70B
    free("free/expiring:free", "Expiring", True, ii=99, expires="2000-01-01"),   # already gone: must be skipped
    free("openrouter/free", "Free router", True),                         # the router: never used
    free("free/tiny-1b:free", "Tiny 1B", False, created=1700000000),     # guess from size: weakest
]


def call(name, **args):
    return {"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def reply(calls=None, text="done"):
    msg = {"role": "assistant", "content": None if calls else text}
    if calls:
        msg["tool_calls"] = calls
        msg["reasoning_details"] = [{"type": "reasoning.text", "text": "thinking"}]   # must never travel to another model
    return {"choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"total_tokens": 120, "cost": 0.0012}}


def script(body):
    msgs = body["messages"]
    prompt = msgs[1]["content"] if isinstance(msgs[1]["content"], str) else ""
    tools = [m for m in msgs if m["role"] == "tool"]
    step = len(tools)
    last = tools[-1]["content"] if tools else ""
    model = body["model"]
    if model == "fake/loop":
        return reply([call("glob", pattern="*")])
    if "suggests an addition or correction to ONE page" in prompt:
        ext = ".html" if "an interactive HTML page" in prompt else ".md"
        if step == 0:
            return reply([call("read_file", path=f"current{ext}")])
        if step == 1:
            add = "<p>Note via OpenRouter.</p>" if ext == ".html" else "\n\nNote via OpenRouter: $|z| = 0$ iff $z = 0$.\n"
            new = last.replace("</body>", add + "</body>") if ext == ".html" else last + add
            return reply([call("write_file", path=f"new{ext}", content=new),
                          call("write_file", path="result.json", content=json.dumps(
                              {"decision": "accept", "reason": "Додано через OpenRouter.", "summary": "Added a note."}))])
        return reply()
    if "look after the MAIN study book" in prompt:
        name = re.search(r"`context/([^`]+)`", prompt).group(1)
        ext = "." + name.rsplit(".", 1)[1]
        if step == 0:
            return reply([call("read_file", path=f"context/{name}")])
        if step == 1:
            add = "<p>Merged via OpenRouter.</p>" if ext == ".html" else "\n\nMerged via OpenRouter: $|z| = 0$ iff $z = 0$.\n"
            new = last.replace("</body>", add + "</body>") if ext == ".html" else last + add
            return reply([call("write_file", path=f"new{ext}", content=new),
                          call("write_file", path="result.json", content=json.dumps(
                              {"verdict": "merge", "match": name, "reason": "Підходить.", "summary": "Adds a note."}))])
        return reply()
    if model == "fake/lazy":
        return reply(text="I think this is fine as it is.")
    if model == "fake/evil-path":
        turn = sum(m["role"] == "assistant" for m in msgs)     # 7 tool results arrive in one go, so count turns
        if turn == 1:
            return reply([call("write_file", path="ok.txt", content="fine")])
        if turn == 0:
            return reply([call("read_file", path="/etc/passwd"), call("write_file", path="../outside.txt", content="pwn"),
                          call("write_file", path="/tmp/sb-outside.txt", content="pwn"),
                          call("read_file", path="../../openrouter.key"), call("grep", pattern="(a+)+$", path="/etc"),
                          call("glob", pattern="../*"), call("nonsense_tool")])
        return reply()
    if model == "fake/bulky":       # a long call: every turn reads a big file and a page, then rewrites a big file
        turn = sum(m["role"] == "assistant" for m in msgs)
        if turn >= 12:
            return reply()
        if turn % 2 == 0:
            return reply([call("read_file", path="big.txt"), call("read_file", path="pages/p001.jpg")])
        return reply([call("write_file", path="out.txt", content=f"turn {turn} " + "x" * 20000)])
    if model == "fake/image":
        if step == 0:
            return reply([call("read_file", path="pages/p001.jpg")])
        return reply([call("write_file", path="seen.txt", content="seen")]) if step == 1 else reply()
    if step == 0:
        return reply([call("write_file", path="hello.txt", content="hi from the model\n"),
                      call("edit_file", path="hello.txt", old_string="hi", new_string="hello")])
    return reply()


def start(port):
    state = {"flaky": 0, "requests": [], "count": {}}
    ctrl = {}          # model id -> {"mode": "429min"|"429day"|"404"|"403"|"500"|"empty", "after": n successful calls first}
    tier = {}          # {"paid": True}: a "freetier" key has bought credits meanwhile

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj, headers=None):
            data = json.dumps(obj).encode()
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _key(self):
            return (self.headers.get("Authorization") or "").removeprefix("Bearer ")

        def do_GET(self):
            if self.path == "/models":
                return self._send(200, {"data": MODELS})
            if self.path == "/key":
                k = self._key()
                if not k.startswith("sk-or-") or "bad" in k:
                    return self._send(401, {"error": {"code": 401, "message": "No auth credentials found"}})
                return self._send(200, {"data": {"label": "test key", "limit_remaining": 5.0, "usage": 0.1,
                                                 "is_free_tier": "freetier" in k and not tier.get("paid")}})
            if self.path == "/credits":
                return self._send(200, {"data": {"total_credits": 0 if "freetier" in self._key() else 10, "total_usage": 0}})
            self._send(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            k = self._key()
            state["requests"].append({"key": k, "body": body})
            if self.path != "/chat/completions":
                return self._send(404, {})
            if "bad" in k:
                return self._send(401, {"error": {"code": 401, "message": "Invalid key"}})
            if "nocredit" in k:
                return self._send(402, {"error": {"code": 402, "message": "Insufficient credits"}})
            if "limit" in k:
                return self._send(429, {"error": {"code": 429, "message": "Rate limited"}})
            if "errbody" in k:
                return self._send(200, {"error": {"code": 429, "message": "Rate limited upstream"}})
            if "flaky" in k and state["flaky"] < 1:
                state["flaky"] += 1
                return self._send(502, {"error": {"code": 502, "message": "Bad gateway"}})
            model, c = body.get("model"), ctrl.get(body.get("model"))
            if c and state["count"].get(model, 0) >= c.get("after", 0):
                mode = c["mode"]
                if mode == "429min":
                    return self._send(429, {"error": {"code": 429, "message": "Rate limit exceeded",
                                                      "metadata": {"error_type": "rate_limit_exceeded"}}},
                                      {"Retry-After": c.get("retry", 30)})
                if mode == "429day":
                    return self._send(429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day",
                                                      "metadata": {"error_type": "rate_limit_exceeded"}}},
                                      {"X-RateLimit-Limit": 50, "X-RateLimit-Remaining": 0,
                                       "X-RateLimit-Reset": int((time.time() + 3 * 3600) * 1000)})
                if mode == "403":
                    return self._send(403, {"error": {"code": 403, "message": f"{model} is only available on agentic harnesses. "
                                                      "Try plugging it into a coding agent listed on https://openrouter.ai/apps"}})
                if mode == "404":
                    return self._send(404, {"error": {"code": 404, "message": "No endpoints found that support tool use"}})
                if mode == "500":
                    return self._send(500, {"error": {"code": 500, "message": "Internal error"}})
                if mode == "empty":
                    state["count"][model] = state["count"].get(model, 0) + 1
                    return self._send(200, reply(text="I will not do that"))
            state["count"][model] = state["count"].get(model, 0) + 1
            self._send(200, script(body))

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.ctrl, srv.count, srv.tier = ctrl, state["count"], tier
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state["requests"]
