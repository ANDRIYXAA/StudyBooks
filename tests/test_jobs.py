"""Offline test: figure edges snapped to blank lines, and long runs (jobs): a rebuild step backs a subject up, takes it
out of the main book and queues its files; the next step starts when the last is done; pause holds the files,
continue releases them; a failed step stops the run and can be retried; undo restores the subject; skip, cancel,
archive. No model is called.

Run from a COPY of the code, with a fresh data dir:
  SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_jobs.py   (never on the real data)"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_SV_PORTS"] = ""
os.environ.setdefault("SB_ADMIN", "owner-example-com")
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "tests" / "fake_claude.py"))

import sb  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# --- 1. snap_box ------------------------------------------------------------------------------------------------------
page = Image.new("L", (1000, 600), 255)
d = ImageDraw.Draw(page)
d.rectangle((100, 40, 700, 70), fill=0)            # "slide title": a dark band
d.rectangle((150, 120, 600, 400), outline=0, width=3)   # the drawing
d.rectangle((560, 395, 640, 420), fill=0)          # a label at its lower right, partly outside the drawing's frame
d.rectangle((150, 520, 800, 540), fill=0)          # a footer line far below
d.ellipse((620, 150, 760, 300), outline=0, width=2)    # a curve that reaches past the label's columns

nb = sb.snap_box(page, (140, 62, 610, 410))        # top cuts the title's bottom, right/bottom cut the label
check("snap: a sliver of the title at the top is cut away", nb[1] >= 71, nb)
check("snap: the label cut at the bottom comes in whole", nb[3] >= 421, nb)
check("snap: the label cut at the right comes in whole", nb[2] >= 641, nb)
check("snap: the footer far below stays out", nb[3] < 520, nb)
clean = (130, 100, 780, 450)
check("snap: a box with blank edges stays as it is", sb.snap_box(page, clean) == clean, sb.snap_box(page, clean))
photo = Image.effect_noise((400, 300), 80).convert("L")
check("snap: an edge inside a photo (no blank line near) stays", sb.snap_box(photo, (50, 50, 350, 250)) == (50, 50, 350, 250),
      sb.snap_box(photo, (50, 50, 350, 250)))

# crop_figs uses it, and SB_FIG_SNAP=0 turns it off
pdir = Path(tempfile.mkdtemp())
page.convert("RGB").save(pdir / "p001.jpg", quality=95)
out = Path(tempfile.mkdtemp())
figs = sb.crop_figs(pdir, [{"id": "f1", "page": 1, "box": [0.14, 0.11, 0.61, 0.68]}], out)
with Image.open(out / "f1.jpg") as im:
    w, h = im.size
check("crop_figs: the figure is kept and snapped (wider than the plain 2 % pad)", figs and w > int((0.61 - 0.14 + 0.04) * 1000) - 2, (w, h))

# --- 2. jobs ----------------------------------------------------------------------------------------------------------
sb.ensure_global()
owner = sb.Space("owner-example-com").ensure(login="owner")
runner = sb.Space("pub-runner").ensure(login="runner")
check("the main book is the owner's", sb.main_space() and sb.main_space().uid == owner.uid)

# old entries of two subjects in the main book, and the source files in the runner's processed uploads
for subj in ("alpha", "beta"):
    e = owner.d["library"] / subj / "topic" / "2026-10-20_old"
    e.mkdir(parents=True)
    (e / "clean.md").write_text("old text of " + subj)
    sb.write_json(e / "meta.json", {"title": "old " + subj, "date": "2026-10-20"})
asis = owner.d["library"] / "alpha" / "lecture-pdfs" / "2026-10-20_asis"
asis.mkdir(parents=True)
sb.write_json(asis / "meta.json", {"title": "as is", "asis": True, "date": "2026-10-20"})
srcs = {}
for n, subj in enumerate(("alpha", "alpha", "beta")):
    sid = f"20261020-12000{n}-abc{n}"
    d = runner.d["processed"] / sid
    d.mkdir(parents=True)
    import pymupdf
    doc = pymupdf.open()
    for _ in range(3 + n):
        doc.new_page()
    doc.save(d / f"0{n}_file.pdf")
    srcs.setdefault(subj, []).append({"src": f"pub-runner/processed/{sid}/0{n}_file.pdf", "date": "2026-10-20", "label": f"file{n}.pdf"})

spec = {"title": {"uk": "Тест", "en": "Test"}, "runner": "pub-runner",
        "steps": [{"kind": "rebuild_subject", "subject": "alpha", "uploads": srcs["alpha"]},
                  {"kind": "rebuild_subject", "subject": "beta", "uploads": srcs["beta"]}]}
bad = sb.job_problems({"title": {"uk": "x"}, "runner": "nobody", "steps": [{"kind": "x"}]})
check("a bad spec is refused with reasons", len(bad) >= 3, bad)
check("a source outside the users folder is refused",
      sb.job_problems(spec | {"steps": [{"kind": "rebuild_subject", "subject": "alpha", "uploads": [{"src": "../../etc/passwd", "date": "2026-10-20"}]}]}))
jid = sb.job_add(spec)
job = sb.job_load(jid)
check("job added: 2 steps, pages counted from the PDFs", [s["pages"] for s in job["steps"]] == [3 + 4, 5], [s["pages"] for s in job["steps"]])

sb.jobs_tick()
job = sb.job_load(jid)
s1, s2 = job["steps"]
check("tick: step 1 runs, step 2 waits", s1["state"] == "running" and s2["state"] == "waiting", (s1["state"], s2["state"]))
check("tick: alpha's rebuilt entries are out of the main book", not (owner.d["library"] / "alpha" / "topic").exists())
check("tick: ... but its 'PDF as is' entry stays", (asis / "meta.json").exists() and s1.get("kept") == 1)
check("tick: ... and backed up", (sb.DATA / s1["backup"] / "topic" / "2026-10-20_old" / "clean.md").exists())
check("tick: beta is not touched yet", (owner.d["library"] / "beta").exists())
q = [u["qid"] for u in s1["uploads"]]
subs = [sb.read_json(runner.d["inbox"] / x / "submission.json") for x in q]
check("tick: 2 uploads in the runner's queue, subject and date set",
      all(s and s["subject"] == "alpha" and s["date"] == "2026-10-20" and s["job"] == jid for s in subs), subs)
check("the queue sees them", sum(1 for _, k, _ in sb.pending_items(runner) if k == "sub") == 2)

# pause holds the waiting files; continue releases them
sb.job_action(jid, "pause")
check("pause: the files are held, the queue skips them", not any(k == "sub" for _, k, _ in sb.pending_items(runner)))
sb.jobs_tick()
check("pause: no step starts or ends", sb.job_load(jid)["steps"][0]["state"] == "running")
sb.job_action(jid, "resume")
check("continue: the files are back in the queue", sum(1 for _, k, _ in sb.pending_items(runner) if k == "sub") == 2)


def finish(qid, ok=True):
    shutil.move(str(runner.d["inbox"] / qid), str(runner.d["processed" if ok else "failed"] / qid))
    if not ok:
        m = sb.read_json(runner.d["failed"] / qid / "submission.json")
        m["last_error"] = "boom"
        sb.write_json(runner.d["failed"] / qid / "submission.json", m)


finish(q[0])
sb.jobs_tick()
check("half done: the step still runs", sb.job_load(jid)["steps"][0]["state"] == "running")
v = sb.jobs_view()["jobs"][0]
check("view: pages done count the finished file", v["steps"][0]["pages_done"] == 3, v["steps"][0])
finish(q[1], ok=False)
sb.jobs_tick()
job = sb.job_load(jid)
check("a failed file stops the run", job["state"] == "failed" and job["steps"][0]["state"] == "failed", job["state"])
check("... with the reason", "boom" in (job["steps"][0]["error"] or ""))
check("... and step 2 has not started", job["steps"][1]["state"] == "waiting" and (owner.d["library"] / "beta").exists())

sb.job_action(jid, "retry", "s1")
check("retry: the failed file is back in the queue", (runner.d["inbox"] / q[1]).exists())
finish(q[1])
# the new entry the worker would have placed
ne = owner.d["library"] / "alpha" / "topic" / "2026-10-20_new"
ne.mkdir(parents=True)
(ne / "clean.md").write_text("new text")
sb.jobs_tick()
job = sb.job_load(jid)
check("step 1 done -> step 2 started in the same tick", job["steps"][0]["state"] == "done" and job["steps"][1]["state"] == "running",
      [s["state"] for s in job["steps"]])
check("beta is now out of the book", not (owner.d["library"] / "beta").exists())

# undo step 1: alpha comes back as it was, the new text is kept in the run's folder
try:
    sb.job_action(jid, "undo", "s2")
    check("undo of a running step is refused", False)
except sb.JobError:
    check("undo of a running step is refused", True)
sb.job_action(jid, "undo", "s1")
job = sb.job_load(jid)
check("undo: alpha is restored from the backup", (owner.d["library"] / "alpha" / "topic" / "2026-10-20_old" / "clean.md").read_text() == "old text of alpha"
      and (asis / "meta.json").exists()
      and not (owner.d["library"] / "alpha" / "topic" / "2026-10-20_new").exists())
check("undo: the rebuilt text is kept", any((sb.JOBS / jid / "undone").rglob("2026-10-20_new")))
check("undo: the step is marked and the run paused", job["steps"][0]["state"] == "undone" and job["paused"])
check("undo: the book is rebuilt", "alpha" in (sb.read_json(owner.dirty, []) or []))

# cancel removes the waiting files; then archive
sb.job_action(jid, "cancel")
job = sb.job_load(jid)
check("cancel: the run and its running step are cancelled", job["state"] == "cancelled" and job["steps"][1]["state"] == "cancelled")
check("cancel: its waiting files left the queue", not any(k == "sub" for _, k, _ in sb.pending_items(runner)))
sb.job_action(jid, "archive")
check("archive: the run left the list", not sb.jobs_view()["jobs"] and (sb.JOBS / "_archive" / jid / "job.json").exists())

# skip, and a run that finishes by itself
two = {"files": [srcs["alpha"][0]["src"], srcs["alpha"][1]["src"]], "date": "2026-10-21", "label": "photos of one lecture"}
jid2 = sb.job_add({"title": {"uk": "Два", "en": "Two"}, "runner": "pub-runner",
                   "steps": [{"kind": "rebuild_subject", "subject": "gamma", "uploads": [two]},
                             {"kind": "rebuild_subject", "subject": "delta", "uploads": srcs["beta"]}]})
check("an upload of several files counts all their pages", sb.job_load(jid2)["steps"][0]["pages"] == 7)
sb.job_action(jid2, "skip", "s2")
sb.jobs_tick()
q2 = sb.job_load(jid2)["steps"][0]["uploads"][0]["qid"]
check("a subject not in the book yet: no backup, files queued", sb.job_load(jid2)["steps"][0]["backup"] is None and (runner.d["inbox"] / q2).exists())
sub2 = sb.read_json(runner.d["inbox"] / q2 / "submission.json")
check("several files go in ONE upload, numbered, all copied",
      sub2["files"] == ["01_00_file.pdf", "02_01_file.pdf"] and all((runner.d["inbox"] / q2 / f).exists() for f in sub2["files"]), sub2["files"])
finish(q2)
sb.jobs_tick()
job = sb.job_load(jid2)
check("skip + last step done -> the run is done", job["state"] == "done" and [s["state"] for s in job["steps"]] == ["done", "skipped"],
      [s["state"] for s in job["steps"]])
check("the log has both languages", all(l.get("uk") and l.get("en") for l in job["log"]))
try:
    sb.job_action("../x", "pause")
    check("a bad id is refused", False)
except sb.JobError as e:
    check("a bad id is refused", e.code == 404)

print("\nALL OK" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
