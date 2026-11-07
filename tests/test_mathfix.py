"""Offline test: the fixes that the app makes to the Typst math that pandoc writes (needs pandoc and typst on this machine).
Run from a COPY of the code, with a fresh data dir: SB_DATA=<tmp> python test_mathfix.py (never on the real data)"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_SV_PORTS"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sb  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


B = "\\|"
check("bars: the spaces inside a pair go", sb.tighten_bars(f"{B} y - x {B} < delta") == f"{B}y - x{B} < delta")
check("bars: two pairs in one formula", sb.tighten_bars(f"{B} a {B} < {B} f(y) - f(x) {B} < e") == f"{B}a{B} < {B}f(y) - f(x){B} < e")
check("bars: a single bar (set builder) is not touched", sb.tighten_bars(f"{{ x {B} x > 0 }}") == f"{{ x {B} x > 0 }}")
check("bars: no bar, no change", sb.tighten_bars("a + b") == "a + b")
check("bars: the space outside the pair stays", sb.tighten_bars(f"a {B} b {B} c") == f"a {B}b{B} c")
typ = sb.md_to_typst("Inline $|a| < |b|$ and\n\n$$\\forall y\\ (|y - x| < \\delta \\Longrightarrow |f(y) - f(x)| < \\epsilon)$$\n\nSet: $\\{x \\mid x > 0\\}$.\n")
check("pandoc text: the bars of the formulas are tight", f"{B}a{B} < {B}b{B}" in typ and f"{B}y - x{B}" in typ and f"{B}f" in typ, typ)
check("a quoted text with bars inside a formula is left alone", sb.fix_typst_symbols('$ "a \\| b \\| c" $') == '$ "a \\| b \\| c" $')
print("\n%d failed" % len(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
