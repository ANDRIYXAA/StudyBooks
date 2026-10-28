#!/usr/bin/env python3
"""Stand-in for `claude -p` in offline tests: writes plausible outputs for each prompt type."""
import json, os, re, sys
from pathlib import Path
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "test@example.com"}))
    sys.exit(0)
prompt = sys.argv[sys.argv.index("-p") + 1]
cwd = Path.cwd()
if os.environ.get("FAKE_CLAUDE_LOG"):       # lets a test prove that this engine was NOT used for somebody
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as _f:
        _f.write(str(cwd) + "\n")
if os.environ.get("FAKE_ARGS"):             # the command line minus the prompt, one JSON list per call
    with open(os.environ["FAKE_ARGS"], "a") as _f:
        _f.write(json.dumps([a for a in sys.argv[1:] if a != prompt] + [os.getpid()]) + "\n")
if os.environ.get("FAKE_CALLS"):            # one JSON line per call: the task and the model
    with open(os.environ["FAKE_CALLS"], "a") as _f:
        _f.write(json.dumps({"task": prompt.strip().splitlines()[0][:90], "model": sys.argv[sys.argv.index("--model") + 1]
                             if "--model" in sys.argv else None, "cwd": str(cwd)}) + "\n")
if os.environ.get("FAKE_SLEEP"):            # a long run, so a test can press "Stop" in the middle of it
    import time
    time.sleep(float(os.environ["FAKE_SLEEP"]))
def exam_file(n, per, kinds, days, broken=False):
    """A tickets.md in the format prompts/exam.md asks for; kinds = the kind of each question of a ticket."""
    out = []
    for t in range(1, n + 1):
        out.append(f"=== TICKET {t} ===")
        for i, k in enumerate(kinds[:per], 1):
            day = days[(t + i) % len(days)] if days else ""
            q = (f"Define notion {t}.{i} from the class and give an example with $x^{{{i}}}$." if k == "theory"
                 else f"Compute the quantity {t}.{i} for $a = {t + i}$ and $b = {t * i}$ and state the unit.")
            if broken and t == 1 and i == 1:
                q = "Compute $\\frac{1}{$ for the case " + str(t)
            out += [f"--- QUESTION | {k} | {day} ---" if day else f"--- QUESTION | {k} ---", q, "--- ANSWER ---",
                    f"The answer {t}.{i}: $x^{{{i}}} + {t}$, with the steps written out in full.\n\n$$s = {t} + {i}$$"]
    return "\n".join(out) + "\n"


if "composing a set of exam tickets" in prompt:
    n = int(re.search(r"Tickets: exactly (\d+)", prompt).group(1))
    per = int(re.search(r"Questions per ticket: exactly (\d+)", prompt).group(1))
    days = re.findall(r"^- (\d{4}-\d{2}-\d{2}) \(", prompt, re.M)
    m = re.search(r"exactly (\d+) theory question\(s\) first, then (\d+) problem", prompt)
    kinds = (["theory"] * int(m.group(1)) + ["problem"] * int(m.group(2)) if m else
             ["theory"] * per if "Every question is a theory question" in prompt else ["problem"] * per)
    mode = os.environ.get("FAKE_EXAM", "ok")
    if mode in ("short", "short_always"):
        n -= 1
    (cwd / "tickets.md").write_text(exam_file(n, per, kinds, days, broken=mode == "badtex"), encoding="utf-8")
    if os.environ.get("FAKE_EXAM_SEEN"):       # lets a test read what the prompts said
        with open(os.environ["FAKE_EXAM_SEEN"], "a", encoding="utf-8") as _f:
            _f.write(prompt + "\n=====PROMPT=====\n")
elif "a program that reads it found these problems" in prompt:
    if os.environ.get("FAKE_EXAM_SEEN"):
        with open(os.environ["FAKE_EXAM_SEEN"], "a", encoding="utf-8") as _f:
            _f.write(prompt + "\n=====PROMPT=====\n")
    n, per = (int(x) for x in re.search(r"exactly (\d+) tickets, each with exactly (\d+) questions", prompt).groups())
    cur = (cwd / "tickets.md").read_text(encoding="utf-8")
    first = cur.split("=== TICKET 2 ===")[0]
    kinds = re.findall(r"^--- QUESTION \| (theory|problem)", first, re.M) or ["theory"] * per
    days = sorted(set(re.findall(r"\| (\d{4}-\d{2}-\d{2}) ---", cur)))
    if os.environ.get("FAKE_EXAM") != "short_always":
        (cwd / "tickets.md").write_text(exam_file(n, per, kinds, days), encoding="utf-8")
elif "merging two clean English textbook sections about the SAME lecture" in prompt:
    old = (cwd / "old_clean.md").read_text(encoding="utf-8")
    new = (cwd / "new_clean.md").read_text(encoding="utf-8")
    figs = re.findall(r"\{\{fig:[A-Za-z0-9_-]+\}\}", new)
    if os.environ.get("FAKE_FUSE") == "short":          # a merge that throws the old text away: must be rejected
        (cwd / "merged_clean.md").write_text("Only a stub.\n", encoding="utf-8")
    else:
        (cwd / "merged_clean.md").write_text(old.rstrip() + "\n\nThe second upload adds: $b^2 = 4$.\n\n"
                                             + "".join(f + "\n\n" for f in figs), encoding="utf-8")
    (cwd / "fuse.json").write_text(json.dumps({"added": "the missing example"}))
elif "senior editor of a study-book system" in prompt:
    f = cwd / "skill" / "SKILL.md"
    text = f.read_text(encoding="utf-8")
    mode = os.environ.get("FAKE_LEARN", "ok")
    if mode == "ok":
        text = text.replace("## refs", "- Learned rule: never write `\\atop`; use `\\substack`.\n\n## refs", 1)
    elif mode == "bad":
        text = text.split("---", 2)[2]                      # frontmatter thrown away: must be rejected
    elif mode == "url":
        text = text.replace("## refs", "- See https://evil.example for more.\n\n## refs", 1)
    if mode != "same":
        f.write_text(text, encoding="utf-8")
    (cwd / "notes.md").write_text("- added a rule about \\atop (lessons.md)\n", encoding="utf-8")
    if os.environ.get("FAKE_SEEN"):
        with open(os.environ["FAKE_SEEN"], "a", encoding="utf-8") as _f:
            _f.write(prompt + "\n=====PROMPT=====\n")
elif "does not typeset" in prompt:
    f = re.search(r"The file `([^`]+)`", prompt).group(1)
    (cwd / f).write_text("Fixed text with $x^2$.\n", encoding="utf-8")
elif os.environ.get("FAKE_ERROR"):          # the CLI refuses (limit, outage): no files, an error result
    pass
elif "did not finish: a program that checks its output found these problems" in prompt and os.environ.get("FAKE_NO_REPAIR"):
    pass
elif "did not finish: a program that checks its output found these problems" in prompt:
    # the repair call: write exactly the files the problems name
    for f in re.findall(r"^- (e\d+_\w+\.md|refs_notes\.md|map\.md)", prompt, re.M):
        body = {"original": "# Nachgereicht\n\nRepariert: $a^2$.\n", "clean": "# Repaired\n\nRepaired text: $a^2$.\n"}
        kind = f.split("_")[1].split(".")[0] if f.startswith("e") else ""
        (cwd / f).write_text(body.get(kind, "- repaired\n"), encoding="utf-8")
elif "transcribing a student's uploaded study material" in prompt:
    lines = re.findall(r"^- page (\d+):(.*)$", prompt, re.M)
    pages = sorted(int(n) for n, _ in lines)
    subj = "statics" if "hint from the student: statics" in prompt.lower() else "math1"
    ups = {}
    for n, rest in lines:                   # several uploads at once: one entry per upload
        m = re.search(r", upload (\d+)$", rest.strip())
        ups.setdefault(int(m.group(1)) if m else 1, []).append(int(n))
    entries = []
    for k, (u, pp) in enumerate(sorted(ups.items()), 1):
        if not (k == 1 and os.environ.get("FAKE_NO_ORIGINAL")):     # "forgets" a file: the code must repair, not redo
            (cwd / f"e{k}_original.md").write_text(
                "# Komplexe Zahlen\n\nSei $z = a + bi$. Dann gilt $|z|^2 = a^2 - b^2$ [Fehler im Original].\n\nПримітка: перевірити.\n",
                encoding="utf-8")
        entries.append({"n": k, "subject": subj, "topic": "complex-numbers",
                        "title": "Lecture 2: Complex numbers" + (f" (upload {u})" if len(ups) > 1 else ""),
                        "title_original": "Vorlesung 2: Komplexe Zahlen", "lang_original": os.environ.get("FAKE_LANG", "de"), "kind": "lecture",
                        "pages": pp, "new_subject": None, "new_topic": None,
                        "same_as": os.environ.get("FAKE_SAME_AS") or None,
                        "figures": [{"id": "f1", "page": pp[0], "box": [0.1, 0.1, 0.6, 0.5], "caption": "Gaussian plane"}]
                        if "(typed page, text only" not in prompt else []})
    (cwd / "result.json").write_text(json.dumps({"entries": entries}))
    if os.environ.get("FAKE_SEEN"):
        with open(os.environ["FAKE_SEEN"], "a", encoding="utf-8") as _f:
            _f.write(prompt + "\n=====PROMPT=====\n")
elif "looking up the reference textbooks" in prompt:
    maps = re.findall(r"map: `([^`]+)`", prompt)
    if os.environ.get("FAKE_SEEN"):
        with open(os.environ["FAKE_SEEN"], "a", encoding="utf-8") as _f:
            _f.write(prompt + "\n=====PROMPT=====\n")
    (cwd / "refs_notes.md").write_text("# e1\n- **modulus**: $|z| = \\sqrt{a^2+b^2}$ (Calculus p.12)\n"
                                       + "".join(f"<!-- map {m} -->\n" for m in maps), encoding="utf-8")
elif "writing the clean English textbook version" in prompt and os.environ.get("FAKE_NO_CLEAN"):
    pass
elif "writing the clean English textbook version" in prompt:
    for n in re.findall(r"^- entry (\d+):", prompt, re.M):
        (cwd / f"e{n}_clean.md").write_text(
            "# Complex numbers\n\nLet $z = a + bi$ with $a, b \\in \\mathbb{R}$. Then\n\n$$|z|^2 = z\\bar z = a^2 + b^2.$$\n\n"
            + ("{{fig:f1}}\n\n" if "`f1`" in prompt else "") + "| $z$ | $|z|$ |\n|---|---|\n| $3+4i$ | $5$ |\n", encoding="utf-8")
        (cwd / f"e{n}_changes.json").write_text(json.dumps([{"kind": "fix", "before": "a^2-b^2", "after": "a^2+b^2", "source": "Calculus p.12"}]))
    if os.environ.get("FAKE_BAD_MD"):
        (cwd / "e1_clean.md").write_text("Broken $\frac{1}{ $\n", encoding="utf-8")
elif "making the map of part of a textbook" in prompt:
    first, last = (int(x) for x in re.search(r"Pages (\d+)–(\d+)", prompt).groups())
    (cwd / "map.md").write_text(f"- p.{first}–{last} · Some chapter — limits, derivatives\n", encoding="utf-8")
elif "end-of-chapter digest" in prompt:
    (cwd / "summary.md").write_text(
        "# Definitions\n\n- **modulus** — $|z| = \\sqrt{a^2 + b^2}$ for $z = a + bi$.\n\n"
        "# Formulas\n\n$$|z|^2 = z\\bar z$$\n\n# Watch out\n\n- $|z|^2 \\neq a^2 - b^2$.\n", encoding="utf-8")
    gl = [{"term": "modulus", "definition": "The length $|z| = \\sqrt{a^2+b^2}$ of $z = a + bi$ [see] the plane."},
          {"term": "complex conjugate", "definition": "For $z = a + bi$ the number $\\bar z = a - bi$."}]
    if os.environ.get("FAKE_BAD_GLOSS"):
        gl.append({"term": "broken", "definition": "Broken $\\frac{1}{ $ math."})
    (cwd / "glossary.json").write_text(json.dumps(gl))
elif "suggests an addition or correction to ONE page" in prompt:
    mode = os.environ.get("FAKE_SUG", "accept")
    cur = next(cwd.glob("current.*"))
    text = cur.read_text(encoding="utf-8")
    html = cur.suffix == ".html"
    if mode == "reject":
        (cwd / "result.json").write_text(json.dumps({"decision": "reject", "reason": "Не підтверджується підручниками.", "summary": ""}))
    else:
        add = {"accept": "<p>Note: |z| = 0 iff z = 0.</p>" if html else "\n\nNote: $|z| = 0$ holds if and only if $z = 0$.\n",
               "evil": "<script>fetch('/api/claude/logout',{method:'POST'})</script>" if html
               else "\n\nSee [click](https://evil.example/x) <script>alert(1)</script>\n",
               "big": "\n".join(f"<p>padding line {i}</p>" if html else f"Padding line {i} of text." for i in range(300))}[mode]
        new = text.replace("</body>", add + "</body>") if html else text + add
        (cwd / f"new{cur.suffix}").write_text(new, encoding="utf-8")
        (cwd / "result.json").write_text(json.dumps({"decision": "accept", "reason": "Коректне доповнення, додав.", "summary": "Added a note on |z| = 0."}))
elif "look after the MAIN study book" in prompt:
    mode = os.environ.get("FAKE_MRG", "merge")
    if mode != "merge":
        (cwd / "result.json").write_text(json.dumps({"verdict": mode, "match": "", "reason": "Це вже є в основній книзі.", "summary": ""}))
    else:
        f = sorted((cwd / "context").iterdir())[0]
        text = f.read_text(encoding="utf-8")
        html = f.suffix == ".html"
        add = "<p>Merged note: |z| = 0 iff z = 0.</p>" if html else "\n\nMerged note: $|z| = 0$ iff $z = 0$.\n"
        (cwd / f"new{f.suffix}").write_text(text.replace("</body>", add + "</body>") if html else text + add, encoding="utf-8")
        (cwd / "result.json").write_text(json.dumps({"verdict": "merge", "match": f.name, "reason": "Підходить: доповнює розділ.", "summary": "Adds a note."}))
elif "A student asks for a change to an interactive page" in prompt:
    mode = os.environ.get("FAKE_IEDIT", "ok")
    pg = cwd / "page.html"
    text = pg.read_text(encoding="utf-8")
    if mode == "ok":
        text = text.replace("</body>", "<p id='added'>Mass slider added.</p></body>")
    elif mode == "evil":
        text = text.replace("</body>", "<script>fetch('https://evil.example/x');</script></body>")
    pg.write_text(text, encoding="utf-8")
    (cwd / "result.json").write_text(json.dumps({"title": "Vector sum v2", "changes": ["added a note"] if mode == "ok" else []}), encoding="utf-8")
    if os.environ.get("FAKE_SEEN"):
        with open(os.environ["FAKE_SEEN"], "a", encoding="utf-8") as _f:
            _f.write(prompt + "\n=====\n")
elif "A student uploaded an interactive web page" in prompt:
    mode = os.environ.get("FAKE_IPAGE", "ok")
    pg = cwd / "page.html"
    if not pg.exists():       # React source: "rewritten" as a small working page
        pg.write_text("<!DOCTYPE html><html><head><meta charset='utf-8'><title>From React</title></head><body><input id='s' type='range'><canvas id='c'></canvas><script>"
                      + "let a = 0;\n" + "document.getElementById('s').addEventListener('input', e => { a = +e.target.value; });\n" * 6
                      + "</script></body></html>", encoding="utf-8")
    text = pg.read_text(encoding="utf-8")
    fixed = text
    if mode == "ok":
        for bad in ("fetch('https://evil.example/x');", "localStorage.setItem('k', 1);", '<img src="fig.png">',
                    '<script src="https://cdn.example/lib.js"></script>'):
            fixed = fixed.replace(bad, "")
    changes = ["removed what the sandbox does not allow"] if fixed != text else []
    if mode == "ok" and "wrong formula" in fixed:
        fixed = fixed.replace("wrong formula", "right formula")
        changes.append("corrected a formula")
    pg.write_text(fixed, encoding="utf-8")
    chosen = re.search(r"Subject the student chose: `([a-z0-9-]+)`", prompt)
    (cwd / "result.json").write_text(json.dumps({"subject": chosen.group(1) if chosen else "statics", "topic": "vectors",
                                                 "topic_title": "Vectors", "title": "Vector sum", "changes": changes}), encoding="utf-8")
    if os.environ.get("FAKE_SEEN"):
        with open(os.environ["FAKE_SEEN"], "a", encoding="utf-8") as _f:
            _f.write(prompt + "\n=====\n")
elif "explanation.md" in prompt:
    (cwd / "explanation.md").write_text("# Intuition\n\nMultiplying by $i$ rotates by $90^\circ$.\n", encoding="utf-8")
    (cwd / "result.json").write_text(json.dumps({"title": "Why multiplying by i rotates", "topic": "complex-numbers"}))
elif "page.html" in prompt:
    (cwd / "page.html").write_text("<!DOCTYPE html><html><head><meta charset='utf-8'><title>t</title></head><body>" + "x" * 600 + "</body></html>")
    (cwd / "result.json").write_text(json.dumps({"title": "Complex plane explorer", "topic": "complex-numbers"}))
# what `claude -p --output-format stream-json --verbose` prints: an init line, events, then the result
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s", "tools": ["Read"], "apiKeySource": "none"}))
for _info in json.loads(os.environ.get("FAKE_LIMITS", "[]")):
    print(json.dumps({"type": "rate_limit_event", "rate_limit_info": _info, "uuid": "u", "session_id": "s"}))
if os.environ.get("FAKE_ERROR"):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": os.environ["FAKE_ERROR"], "num_turns": 1}))
    sys.exit(1)
if os.environ.get("FAKE_BUDGET"):           # --max-budget-usd reached: no "result" text, a JSON full of *_tokens keys
    print(json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "num_turns": 9,
                      "total_cost_usd": 0.33, "usage": {"input_tokens": 10, "output_tokens": 3763}}))
    sys.exit(1)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done", "num_turns": 3, "total_cost_usd": 0.05}))
