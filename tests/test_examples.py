"""Offline test: example containers of the clean book. The format and its checks, the SVG checks, the pass with a fake
model (containers, redrawn diagrams, the comparison with the photo, CHECK), the staleness, the runs of the Admin tab,
and the PDF book (needs typst on this machine).
Run from a COPY of the code, with a fresh data dir: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_examples.py
(never on the real data)"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "0"
os.environ["SB_SV_PORTS"] = ""
os.environ["SB_ILLUSTRATE"] = "0"          # the pictures for plain blocks have their own test (test_illustrate.py)
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "fake_claude.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import examples as ex  # noqa: E402
import sb  # noqa: E402
import web  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


GOOD = """# Lecture

Intro text.

=== EXAMPLE | Modulus of a number ===
--- PROBLEM ---
Compute $|z|$ for $z = 3 + 4i$.
--- FIGURE ---
{{draw:d1}}
--- SOLUTION ---
$|z| = \\sqrt{3^2 + 4^2} = 5$.
--- EXPLANATION ---
The modulus is the length of the arrow. Use the rule of Pythagoras.
--- CHECK ---
The sign in the last line looks wrong.
=== END ===

After text.
"""

# ---------------------------------------------------------------- the format
segs = ex.parse(GOOD)
check("parse: text, one container, text", [k for k, _ in segs] == ["text", "example", "text"] and segs[1][1]["title"] == "Modulus of a number")
check("parse: the parts are cut out", list(segs[1][1]["sections"]) == ["PROBLEM", "FIGURE", "SOLUTION", "EXPLANATION", "CHECK"]
      and segs[1][1]["sections"]["FIGURE"] == "{{draw:d1}}")
for why, bad in (("a container inside a container", "=== EXAMPLE | A ===\n--- PROBLEM ---\nx\n=== EXAMPLE | B ===\n"),
                 ("no end", "=== EXAMPLE | A ===\n--- PROBLEM ---\nx\n"),
                 ("an end without a start", "text\n=== END ===\n"),
                 ("a part twice", "=== EXAMPLE | A ===\n--- PROBLEM ---\nx\n--- PROBLEM ---\ny\n=== END ==="),
                 ("parts out of order", "=== EXAMPLE | A ===\n--- SOLUTION ---\nx\n--- PROBLEM ---\ny\n=== END ==="),
                 ("text before the first part", "=== EXAMPLE | A ===\nloose text\n--- PROBLEM ---\ny\n=== END ===")):
    try:
        ex.parse(bad)
        check(f"parse refuses {why}", False)
    except ex.ExampleError:
        check(f"parse refuses {why}", True)
check("a horizontal rule inside a part is text, not a part marker", ex.parse("=== EXAMPLE | A ===\n--- PROBLEM ---\nx\n---\ny\n=== END ===")[0][1]["sections"]["PROBLEM"] == "x\n---\ny")
check("structure: a good container passes", ex.structure_problems(segs) == [])
no_expl = "=== EXAMPLE | A ===\n--- PROBLEM ---\nx\n=== END ==="
check("structure: the explanation is required", any("EXPLANATION" in p for p in ex.structure_problems(ex.parse(no_expl))))
bad_link = "=== EXAMPLE | A ===\n--- PROBLEM ---\nx\n--- EXPLANATION ---\nsee [here](http://x.example)\n=== END ==="
check("structure: no link in the explanation", any("link" in p for p in ex.structure_problems(ex.parse(bad_link))))
check("structure: no figure marker in the explanation", any("figure markers" in p for p in ex.structure_problems(
    ex.parse("=== EXAMPLE | A ===\n--- PROBLEM ---\nx\n--- EXPLANATION ---\n{{fig:f1}}\n=== END ==="))))

OLD = "Intro text.\n\nCompute $|z|$ for $z = 3 + 4i$.\n{{fig:f1}}\n$|z| = \\sqrt{3^2 + 4^2} = 5$.\n\nAfter text.\n"
check("preservation: the same text and formulas pass", ex.preservation_problems(OLD, ex.core_text(segs).replace("{{draw:d1}}", "")) == [])
check("preservation: a changed formula is refused", any("formulas" in p for p in ex.preservation_problems(OLD, OLD.replace("3 + 4i", "3+4i+0"))))
check("preservation: a formula with other spaces is the same formula", ex.preservation_problems(OLD, OLD.replace("3 + 4i", "3+4i")) == [])
check("preservation: a rewritten text is refused", any("words" in p for p in ex.preservation_problems(OLD * 1, "Intro text.\n\nPlease determine the absolute size of the quantity $|z|$ for $z = 3 + 4i$.\n{{fig:f1}}\n$|z| = \\sqrt{3^2 + 4^2} = 5$.\n\nAfter text.\n")))
check("figures: replaced by a draw is fine, lost is not", ex.figure_problems(OLD, GOOD, {"d1": "f1"}) == []
      and any("lost" in p for p in ex.figure_problems(OLD, GOOD.replace("{{draw:d1}}", ""), {"d1": "f1"})))
check("figures: a draw must say which figure it redraws", any("draw_of" in p for p in ex.figure_problems(OLD, GOOD, {})))
check("figures: both the photo and the redraw is refused", any("twice" in p for p in ex.figure_problems(OLD, GOOD.replace("{{draw:d1}}", "{{draw:d1}}\n{{fig:f1}}"), {"d1": "f1"})))
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><line x1="1" y1="1" x2="9" y2="9" stroke="#222"/><text x="5" y="5">F1</text></svg>'
check("svg: a plain drawing passes", ex.svg_problems(SVG) == [], ex.svg_problems(SVG))
for why, bad in (("a script", SVG.replace("</svg>", "<script>alert(1)</script></svg>")),
                 ("an event handler", SVG.replace("<line ", '<line onload="x()" ')),
                 ("an image", SVG.replace("</svg>", '<image href="http://x.example/a.png"/></svg>')),
                 ("an outside href", SVG.replace("</svg>", '<use href="http://x.example/a.svg#b"/></svg>')),
                 ("a foreignObject", SVG.replace("</svg>", "<foreignObject/></svg>")),
                 ("a DOCTYPE", '<!DOCTYPE svg [<!ENTITY a "b">]>' + SVG), ("no viewBox", SVG.replace('viewBox="0 0 200 100"', "")),
                 ("a huge viewBox", SVG.replace("0 0 200 100", "0 0 90000 100")), ("broken XML", SVG.replace("</svg>", "")),
                 ("a url() outside the drawing", SVG.replace('stroke="#222"', 'fill="url(http://x.example/g)"')),
                 ("too large", SVG.replace("</svg>", "<!--" + "x" * 70000 + "--></svg>")), ("empty", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"/>')):
    check(f"svg refuses {why}", ex.svg_problems(bad) != [])

# ---------------------------------------------------------------- the pass with the fake model
sb.set_lang("uk")
main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
SUBJ = "math1"
TEXT = ("# Lecture on complex numbers\n\nThe number $i$ is defined by $i^2 = -1$ and every complex number is $z = a + bi$ with the real part $a$ and "
        "the imaginary part $b$. The modulus of $z$ is $|z| = \\sqrt{a^2 + b^2}$ and the conjugate is $\\bar z = a - bi$. This is theory that "
        "the lecture explains before the example.\n\nExample 1: compute the modulus of $z = 3 + 4i$.\nStep: $|z| = \\sqrt{3^2 + 4^2} = 5$.\n"
        "{{fig:f1}}\n\nMore theory after the example: the product of two numbers has the modulus $|z w| = |z||w|$ and this is used a lot.\n")


def make_entry(name, text=TEXT, added=None, date="2026-11-10", topic="complex-numbers", figures=True):
    d = main.d["library"] / SUBJ / topic / name
    (d / "figs").mkdir(parents=True, exist_ok=True)
    meta = {"title": "Lecture " + name, "topic": topic, "date": date, "added": added or sb.now_iso(), "order": 0, "kind": "lecture", "pages": []}
    if figures:
        from PIL import Image
        Image.new("RGB", (300, 200), (240, 240, 240)).save(d / "figs" / "f1.jpg")
        meta["figures"] = [{"id": "f1", "caption": "A triangle"}]
    sb.write_json(d / "meta.json", meta)
    (d / "clean.md").write_text(text, encoding="utf-8")
    (d / "original.md").write_text(text, encoding="utf-8")
    return d


def entry(d):
    return next(m for m in sb.entries_of(main, SUBJ) if m["dir"] == d)


def run(name_env):
    for k in ("FAKE_EXAMPLES", "FAKE_DRAW", "FAKE_VERIFY"):
        os.environ.pop(k, None)
    os.environ.update(name_env)
    return sb.update_examples(main)


since = sb.examples_state()["since"]
check("the state has a starting moment; older sections are not made by themselves", bool(since))
d_old = make_entry("old", added="2020-01-01T10:00:00+01:00")
d1 = make_entry("e1")
check("a new section is found as stale, an old one is not", [m["dir"].name for _, m, _ in sb.stale_examples(main, since, set())] == ["e1"])
check("... and an old one is found when its subject has a run", {m["dir"].name for _, m, _ in sb.stale_examples(main, since, {SUBJ})} == {"e1", "old"})

# a model that writes the same text without containers: refused, repaired once, then recorded as a failure
os.environ["FAKE_CALLS"] = str(Path(os.environ["SB_DATA"]) / "calls.txt")
err = run({"FAKE_EXAMPLES": "rewrite"})
fl = sb.read_json(sb.examples_failures_file(main), {})
check("a rewritten example text is refused (and repaired once), no containers are stored", err is None and not (d1 / "examples").exists()
      and sum(f["attempts"] for f in fl.values()) == 1, (err, fl))
calls = [json.loads(l) for l in Path(os.environ["FAKE_CALLS"]).read_text().splitlines()]
check("... two model calls were made, on the role model", len(calls) == 2 and all(c["model"] == "sonnet" for c in calls), calls)
Path(os.environ["FAKE_CALLS"]).unlink()
err = run({"FAKE_EXAMPLES": "formula"})
check("a changed formula is refused", not (d1 / "examples").exists())
sb.write_json(sb.examples_failures_file(main), {})
err = run({"FAKE_EXAMPLES": "nofile"})
check("no file written: refused, nothing stored", not (d1 / "examples").exists())
sb.write_json(sb.examples_failures_file(main), {})

# the good run, with a CHECK and no drawing (no FAKE_DRAW: the photo stays)
err = run({"FAKE_EXAMPLES": "check"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("containers are made: one example, a CHECK, the photo kept", err is None and mt.get("examples") == 1 and mt.get("checks") == 1 and mt.get("drawn") == []
      and mt.get("sha") == sb.examples_source_sha(d1), mt)
txt = (d1 / "examples" / "clean_ex.md").read_text(encoding="utf-8")
check("... the text has a container with the explanation and the photo marker", "=== EXAMPLE |" in txt and "--- EXPLANATION ---" in txt and "{{fig:f1}}" in txt and "--- CHECK ---" in txt)
check("... the text outside the container is untouched", txt.startswith("# Lecture on complex numbers") and "More theory after the example" in txt)
check("the old section was not touched (not made by itself)", not (d_old / "examples").exists())
check("examples_for_book gives the containers while the clean text is the same", sb.examples_for_book(main, entry(d1)) == txt)
(d1 / "clean.md").write_text(TEXT + "\nOne more line.\n", encoding="utf-8")
check("... and nothing when the clean text changed (stale)", sb.examples_for_book(main, entry(d1)) is None and not sb.examples_current(d1))
(d1 / "clean.md").write_text(TEXT, encoding="utf-8")
check("... and it is current again with the same text", sb.examples_current(d1))

# the book: the PDF compiles with the box
sb.build_book(main, SUBJ, "clean")
typ = (main.d["build"] / SUBJ / "clean.typ").read_text(encoding="utf-8")
check("the clean book has the example box with the number, the title and the check", "#examplebox(1," in typ and "A worked example" in typ and "CHECK" in typ and "EXPLANATION" in typ)
check("... and the PDF was built", (main.d["books"] / SUBJ / "clean.pdf").stat().st_size > 1000)
sb.build_book(main, SUBJ, "original")
check("the original book has no containers", "examplebox(1," not in (main.d["build"] / SUBJ / "original.typ").read_text(encoding="utf-8"))

# drawings: good and passing the check, failing (photo), fixable, unsafe
shutil_rm = __import__("shutil").rmtree


def again(env):
    shutil_rm(d1 / "examples", ignore_errors=True)
    sb.write_json(sb.examples_failures_file(main), {})
    Path(os.environ["FAKE_CALLS"]).unlink(missing_ok=True)
    return run(env)


err = again({"FAKE_EXAMPLES": "ok", "FAKE_DRAW": "good", "FAKE_VERIFY": "ok"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
txt = (d1 / "examples" / "clean_ex.md").read_text(encoding="utf-8")
check("a redraw that passes the check is kept: marker, file and meta", mt.get("drawn") == ["d1"] and (d1 / "examples" / "draw" / "d1.svg").exists()
      and "{{draw:d1}}" in txt and "{{fig:f1}}" not in txt, (mt, txt[-300:]))
calls = [json.loads(l) for l in Path(os.environ["FAKE_CALLS"]).read_text().splitlines()]
check("... the model made one call for the containers and one for the check (vision)", len(calls) == 2, calls)
sb.build_book(main, SUBJ, "clean")
typ = (main.d["build"] / SUBJ / "clean.typ").read_text(encoding="utf-8")
check("the book shows the drawing in the box and the PDF builds", "examples/draw/d1.svg" in typ and (main.d["books"] / SUBJ / "clean.pdf").exists())

err = again({"FAKE_EXAMPLES": "ok", "FAKE_DRAW": "good", "FAKE_VERIFY": "bad"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
txt = (d1 / "examples" / "clean_ex.md").read_text(encoding="utf-8")
check("a redraw that does not pass (after one fix) goes back to the photo", mt.get("drawn") == [] and mt.get("photo") == ["d1"] and "{{fig:f1}}" in txt
      and "{{draw:d1}}" not in txt and not (d1 / "examples" / "draw" / "d1.svg").exists(), (mt, txt[-200:]))
calls = [json.loads(l) for l in Path(os.environ["FAKE_CALLS"]).read_text().splitlines()]
check("... containers, check, fix, check again: four calls", len(calls) == 4, [c["task"][:40] for c in calls])

err = again({"FAKE_EXAMPLES": "ok", "FAKE_DRAW": "good", "FAKE_VERIFY": "fixable"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("a redraw that passes after the fix is kept", mt.get("drawn") == ["d1"] and mt.get("photo") == [], mt)
err = again({"FAKE_EXAMPLES": "ok", "FAKE_DRAW": "good", "FAKE_VERIFY": "silent"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("a checker that writes no answer does not confirm the redraw: the photo stays", mt.get("drawn") == [] and mt.get("photo") == ["d1"], mt)
err = again({"FAKE_EXAMPLES": "ok", "FAKE_DRAW": SVG.replace("</svg>", "<script>alert(1)</script></svg>"), "FAKE_VERIFY": "ok"})
check("an unsafe SVG is refused before anything is stored", not (d1 / "examples").exists())
err = again({"FAKE_EXAMPLES": "none"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("a section with no example gets a note and no containers; the book keeps the plain text", mt.get("examples") == 0 and not (d1 / "examples" / "clean_ex.md").exists()
      and sb.examples_for_book(main, entry(d1)) is None and sb.examples_current(d1), mt)
os.environ.pop("FAKE_EXAMPLES", None)
check("a short section is not eligible", not sb.examples_eligible({"dir": make_entry("tiny", text="# Tiny\n\nExample 1: x.\n", added=sb.now_iso()), "added": sb.now_iso()}, since, set()))

# ---------------------------------------------------------------- the runs of the Admin tab
web.need_admin = lambda request: None
web.kick = lambda: None
view = sb.examples_runs_view()
check("the view lists the subjects and no runs", view["runs"] == [] and any(s["id"] == SUBJ for s in view["subjects"]))
sb.examples_run_action(SUBJ, "start")
v = next(r for r in sb.examples_runs_view()["runs"] if r["subject"] == SUBJ)
check("a run counts the sections of the subject (old ones too)", v["state"] == "running" and v["total"] >= 2, v)
try:
    sb.examples_run_action(SUBJ, "start")
    check("a second run of the same subject is refused", False)
except sb.ExamplesError:
    check("a second run of the same subject is refused", True)
sb.examples_run_action(SUBJ, "pause")
shutil_rm(d1 / "examples", ignore_errors=True)
sb.write_json(sb.examples_failures_file(main), {})
err = run({"FAKE_EXAMPLES": "ok"})
check("a paused run makes nothing for the old sections", not (d_old / "examples").exists())
sb.examples_run_action(SUBJ, "resume")
err = run({"FAKE_EXAMPLES": "ok"})
v = next(r for r in sb.examples_runs_view()["runs"] if r["subject"] == SUBJ)
check("a running run makes the old section too, and finishes", (d_old / "examples" / "meta.json").exists() and v["state"] == "done" and v["done"] >= 2 and v["containers"] >= 2, v)
sb.examples_run_action(SUBJ, "clear")
check("a finished run can be removed", not sb.examples_runs_view()["runs"])
sb.examples_run_action(SUBJ, "start")
sb.examples_run_action(SUBJ, "stop")
check("a stopped run is stopped", next(r for r in sb.examples_runs_view()["runs"])["state"] == "stopped")
try:
    sb.examples_run_action("no-such-subject", "start")
    check("an unknown subject is refused", False)
except sb.ExamplesError:
    check("an unknown subject is refused", True)


def call(fn, *a):
    try:
        return asyncio.run(fn(*a)), None
    except web.HTTPException as e:
        return None, e.status_code


class R:
    pass


res, code = call(web.api_examples_action, R(), SUBJ, "bogus")
check("the endpoint refuses an unknown action with 400", code == 400, (res, code))
check("the Admin page has the examples card and the strings in both languages", 'id="exrCard"' in web.INDEX.read_text(encoding="utf-8")
      and "/api/admin/examples" in web.INDEX.read_text(encoding="utf-8"))

# ---------------------------------------------------------------- the skill and the roles
seed = (Path(__file__).resolve().parent / "skills" / "studybooks-material" / "SKILL.md").read_text(encoding="utf-8")
check("the material skill has the examples section with the owner's rules", "examples" in sb.SKILL_SECTIONS and sum("[owner]" in l for l in sb.skill_sections(seed)["examples"].splitlines()) == 5)
check("the calls for examples and for the check of drawings get that section", "For this task (examples)" in sb.agent_brief("examples") and "For this task (examples)" in sb.agent_brief("verify"))
check("both roles run on a cheap model", sb.ROLE_MODELS.get("examples") == "sonnet" and sb.ROLE_MODELS.get("verify") == "sonnet")

print("\n%d failed" % len(fails) if fails else "\nall examples checks passed")
sys.exit(1 if fails else 0)
