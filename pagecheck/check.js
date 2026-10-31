"use strict";
/* node check.js new.html [old.html]  ->  one JSON line {"new": {...}, "old": {...}}
   Each page runs in its own child process (run.js) with a time limit: a page with an endless loop or a memory blow-up
   gives an error here instead of freezing the checker. The old page (when given) is the baseline: the caller reports only
   what the new page does worse. */
const { spawnSync } = require("child_process");
const path = require("path");

const LIMIT_MS = 25000;

function runOne(file) {
  const r = spawnSync(process.execPath, ["--max-old-space-size=200", path.join(__dirname, "run.js"), file],
    { timeout: LIMIT_MS, killSignal: "SIGKILL", maxBuffer: 1 << 20, encoding: "utf8" });
  if (r.error && r.error.code === "ETIMEDOUT" || r.signal === "SIGKILL") {
    return { errors: [{ message: `the page hangs: it did not finish loading and using its controls in ${LIMIT_MS / 1000} s (an endless loop?)`, phase: "while it ran" }], steps: 0 };
  }
  const out = (r.stdout || "").trim().split("\n").pop();
  try { return JSON.parse(out); } catch (e) {
    return { errors: [{ message: "the page crashed the checker: " + String((r.stderr || out || "no output").slice(0, 200)), phase: "while it ran" }], steps: 0 };
  }
}

const res = { new: runOne(process.argv[2]) };
if (process.argv[3]) res.old = runOne(process.argv[3]);
process.stdout.write(JSON.stringify(res) + "\n");
