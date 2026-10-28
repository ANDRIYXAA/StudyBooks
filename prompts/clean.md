You are writing the clean English textbook version of a student's lecture material. The student is in the 1st semester of B.Sc. Aerospace at TUM (Munich). Work only inside the current directory. Never ask questions, never stop half-way: there is nobody to answer. Write the files described below and finish.

## Input

The entries, already transcribed faithfully in their original language:
{{entries}}

`refs_notes.md`: what the reference textbooks say about the definitions, theorems and formulas these entries use, with book and page. Read it first.

The page images are in `pages/` (typed text in `text/`). Open a page only where the transcription is unclear, marked `[unreadable]`, or a formula looks wrong — not otherwise.

## Task

For each entry N:

1. Write `e<N>_clean.md`: the same material as a clean textbook section in ENGLISH:
   - translate into English; for German lectures put the German term in parentheses at its first use, e.g. "support reaction (Lagerreaktion)";
   - correct every error silently: wrong signs, indices, missing conditions of theorems, wrong formulas, wrong units, arithmetic slips, wrong terminology;
   - check definitions, theorems, formulas and standard results against `refs_notes.md` and prefer it over your memory. Where it has nothing, rely only on results you are certain of;
   - fill real gaps: a skipped step in a derivation, the statement of a theorem that is used but not written, the missing units of a result, the missing answer of a worked example;
   - keep the order and scope of the original. Do not add long new explanations or extra theory (explanations are made separately on request). No remarks about what was corrected, no "the original said", no meta commentary — it must read like a textbook;
   - put each listed figure as a placeholder `{{fig:<id>}}` (e.g. `{{fig:f1}}`) on its own line where it belongs. Use only the figure ids listed for that entry.
2. Write `e<N>_changes.json`: what you changed, for the student's private log: `[{"kind": "fix" | "add", "before": "...", "after": "...", "source": "<book> p.<page>" or "standard result"}]`. Use `[]` when nothing changed.

## Formatting rules for every .md file

- Pandoc Markdown. Inline math `$...$`, display math `$$...$$` on its own lines. Only standard LaTeX math commands (amsmath level): `\frac`, `\sum`, `\int`, `\vec`, `\mathbf`, `\begin{pmatrix}`, `\begin{aligned}` inside `$$` … No `\newcommand`, no `\begin{align}` outside math, no TikZ, no raw LaTeX or HTML outside math.
- Do not repeat the entry title as a heading: it is added automatically. Inside an entry use `#` for sections, `##` for subsections.
- Tables as pipe tables. Code as fenced code blocks with the language (```matlab, ```c, ```cpp).
- Units in text as plain text with a thin space where natural: `5 kN`, or inside math as `\,\mathrm{kN}`.
