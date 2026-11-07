"""Offline test: pictures for the plain blocks of a section (formulas + text, no example). The choice of blocks, the checks
of the model's files, the comparison of each picture with its block, the insertion of the markers (the text is never
rewritten), the storage, and the PDF book (needs typst on this machine).
Run from a COPY of the code, with a fresh data dir: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_illustrate.py
(never on the real data)"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "0"
os.environ["SB_SV_PORTS"] = ""
os.environ["SB_ILLUSTRATE"] = "1"
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "fake_claude.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sb  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- the choice of blocks
MD = ("# Functions\n\n"
      "A function $f$ is continuous at $x_0$ when the limit of $f(x)$ for $x \\to x_0$ equals $f(x_0)$ and this holds for every sequence.\n\n"
      "Short line.\n\n"
      "=== EXAMPLE | Limit ===\n--- PROBLEM ---\nCompute the limit of $\\frac{\\sin x}{x}$ for $x \\to 0$ with the rule of the lecture.\n"
      "--- EXPLANATION ---\nUse the known limit of the sine quotient.\n=== END ===\n\n"
      "The derivative of $f$ at $x_0$ is the limit of the difference quotient $\\frac{f(x_0+h)-f(x_0)}{h}$ for $h \\to 0$ when it exists.\n\n"
      "{{fig:f1}}\n\n"
      "After a figure comes this long paragraph about the tangent line of the graph of $f$ at the point $x_0$ with slope $f'(x_0)$.\n")
blocks = sb.illus_blocks(MD)
texts = [t for _, _, t in blocks]
check("blocks: the long plain paragraph and the one before a figure marker are skipped, the rest chosen",
      len(blocks) == 1 and texts[0].startswith("A function $f$ is continuous"), [t[:30] for t in texts])
lines = MD.split("\n")
check("blocks: the line numbers point at the paragraph", "\n".join(lines[blocks[0][0]:blocks[0][1]]) == texts[0])
check("blocks: nothing from inside an example container", all("sine quotient" not in t for t in texts))

# ---------------------------------------------------------------- the pass with the fake model
sb.set_lang("uk")
main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
SUBJ = "math1"
PLAIN = ("# Continuity\n\nA function $f$ is continuous at $x_0$ when the limit of $f(x)$ for $x \\to x_0$ equals $f(x_0)$ and this holds for "
         "every sequence.\n\nThe derivative of $f$ at $x_0$ is the limit of the difference quotient $\\frac{f(x_0+h)-f(x_0)}{h}$ for $h \\to 0$ "
         "when it exists, and the tangent line has this slope.\n")
WITH_EX = ("# Lecture\n\nA function $f$ is continuous at $x_0$ when the limit of $f(x)$ for $x \\to x_0$ equals $f(x_0)$ and this holds for every "
           "sequence.\n\nExample 1: compute the modulus of $z = 3 + 4i$.\nStep: $|z| = \\sqrt{3^2 + 4^2} = 5$.\n\n"
           "The theory goes on: the conjugate of $z = a + bi$ is $\\bar z = a - bi$ and the product $z \\bar z = |z|^2$ is real and not negative.\n")


def make_entry(name, text, added=None, date="2026-11-10", topic="continuity"):
    d = main.d["library"] / SUBJ / topic / name
    (d / "figs").mkdir(parents=True, exist_ok=True)
    sb.write_json(d / "meta.json", {"title": "Lecture " + name, "topic": topic, "date": date, "added": added or sb.now_iso(),
                                    "order": 0, "kind": "lecture", "pages": []})
    (d / "clean.md").write_text(text, encoding="utf-8")
    (d / "original.md").write_text(text, encoding="utf-8")
    return d


def run(env):
    for k in ("FAKE_EXAMPLES", "FAKE_ILLUS", "FAKE_ILLUS_VERIFY", "FAKE_DRAW", "FAKE_VERIFY"):
        os.environ.pop(k, None)
    os.environ.update(env)
    return sb.update_examples(main)


def reset(d):
    shutil.rmtree(d / "examples", ignore_errors=True)
    sb.write_json(sb.examples_failures_file(main), {})


def entry(d):
    return next(m for m in sb.entries_of(main, SUBJ) if m["dir"] == d)


def strip_markers(t):
    return re.sub(r"\n*\{\{draw:i\d+\}\}\n*", "\n\n", t).strip()


d1 = make_entry("p1", PLAIN)
os.environ["FAKE_CALLS"] = str(Path(os.environ["SB_DATA"]) / "calls.txt")

# a section with no example: the picture goes after block 1 and the text is the same
err = run({"FAKE_EXAMPLES": "none", "FAKE_ILLUS": "ok"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("a plain section gets one picture; no containers", err is None and mt.get("illustrations") == ["i1"] and mt.get("examples") == 0, mt)
txt = (d1 / "examples" / "clean_ex.md").read_text(encoding="utf-8") if (d1 / "examples" / "clean_ex.md").exists() else ""
check("... the marker stands right after block 1", re.search(r"every sequence\.\n\n\{\{draw:i1\}\}\n\n", txt) is not None, txt)
check("... the text without the marker is exactly the old text", strip_markers(txt) == PLAIN.strip(), (strip_markers(txt), PLAIN))
check("... the svg is stored with the section", (d1 / "examples" / "draw" / "i1.svg").exists())
check("... examples_for_book gives the text although there is no container", sb.examples_for_book(main, entry(d1)) == txt)
sb.build_book(main, SUBJ, "clean")
typ = (main.d["build"] / SUBJ / "clean.typ").read_text(encoding="utf-8")
check("... the book shows the picture and the PDF is built", "examples/draw/i1.svg" in typ and (main.d["books"] / SUBJ / "clean.pdf").stat().st_size > 1000)

# the check fails: the picture is given up, the text stays as it was
reset(d1)
err = run({"FAKE_EXAMPLES": "none", "FAKE_ILLUS": "ok", "FAKE_ILLUS_VERIFY": "fail"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("a picture that fails the check is given up", err is None and mt.get("illustrations") == [] and mt.get("illustrations_dropped") == ["i1"], mt)
check("... the book keeps the plain clean text", sb.examples_for_book(main, entry(d1)) is None and not (d1 / "examples" / "draw" / "i1.svg").exists())

# the check fails once, the fix passes
reset(d1)
Path(os.environ["FAKE_CALLS"]).unlink(missing_ok=True)
err = run({"FAKE_EXAMPLES": "none", "FAKE_ILLUS": "ok", "FAKE_ILLUS_VERIFY": "fixable"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("a fixable picture is fixed once and kept", mt.get("illustrations") == ["i1"], mt)
check("... the checker ran twice and the fixer once (the roles of the models)", Path(os.environ["FAKE_CALLS"]).exists())

# the model's file is wrong: repaired once, then no picture, and the section is still made
for mode in ("nofile", "dup"):
    reset(d1)
    err = run({"FAKE_EXAMPLES": "none", "FAKE_ILLUS": mode})
    mt = sb.read_json(d1 / "examples" / "meta.json", {})
    check(f"a wrong ill.json ({mode}) gives no picture, no failure of the section",
          err is None and mt.get("illustrations") == [] and mt.get("sha") == sb.examples_source_sha(d1), (err, mt))

# no block needs a picture
reset(d1)
err = run({"FAKE_EXAMPLES": "none", "FAKE_ILLUS": "none"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("no picture is a normal result", err is None and mt.get("illustrations") == [] and sb.examples_for_book(main, entry(d1)) is None, mt)

# two pictures for two blocks: the markers go to the right places (the last block first, lines do not shift)
reset(d1)
err = run({"FAKE_EXAMPLES": "none", "FAKE_ILLUS": "two"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
txt = (d1 / "examples" / "clean_ex.md").read_text(encoding="utf-8") if (d1 / "examples" / "clean_ex.md").exists() else ""
check("two pictures after two blocks, text unchanged", mt.get("illustrations") == ["i1", "i2"] and strip_markers(txt) == PLAIN.strip()
      and txt.index("{{draw:i1}}") < txt.index("The derivative") < txt.index("{{draw:i2}}"), (mt, txt))

# a section with an example: the container stays, the picture goes only to the plain block
d2 = make_entry("p2", WITH_EX)
err = run({"FAKE_EXAMPLES": "ok", "FAKE_ILLUS": "ok"})
mt = sb.read_json(d2 / "examples" / "meta.json", {})
txt2 = (d2 / "examples" / "clean_ex.md").read_text(encoding="utf-8") if (d2 / "examples" / "clean_ex.md").exists() else ""
check("an example and a picture in one section", mt.get("examples") == 1 and mt.get("illustrations") == ["i1"]
      and "=== EXAMPLE |" in txt2 and "{{draw:i1}}" in txt2, (mt, txt2))
check("... the picture is outside the container", txt2.index("{{draw:i1}}") < txt2.index("=== EXAMPLE |"))

# the switch off
sb.ILLUSTRATE = False
reset(d1)
err = run({"FAKE_EXAMPLES": "none", "FAKE_ILLUS": "ok"})
mt = sb.read_json(d1 / "examples" / "meta.json", {})
check("SB_ILLUSTRATE=0: no picture", mt.get("illustrations") == [], mt)
sb.ILLUSTRATE = True

print("\n%d failed" % len(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
