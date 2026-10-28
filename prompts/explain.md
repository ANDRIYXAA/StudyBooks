A 1st-semester B.Sc. Aerospace student at TUM asks for an explanation for their textbook on **{{subject}}**.

Their request: {{request}}
Topic: {{topic}}

The current textbook entries of this subject are in `context/<topic>/<entry>.md` — read the ones that matter (Grep helps). Reference textbooks are in {{refs}} (`<subject>/<book>/p0123.txt` = page 123):
{{refs_list}}

Write `explanation.md` in English, as a section the student can read on its own:
- start with the intuition: what this is and why it works, in plain words;
- then the precise statement / method in the notation used in the student's material;
- then one fully worked example with numbers (aerospace flavour where it fits naturally);
- end with 2–4 typical mistakes to avoid;
- usually 0.5–2 pages; verify formulas against the reference textbooks where they cover it.

Formatting: Pandoc Markdown, math as `$...$` / `$$...$$` with standard LaTeX commands only, no raw LaTeX/HTML outside math, `#` for its sections (no title heading — the title is added automatically).

Then write `result.json`: `{"title": "<short English title>", "topic": "<topic id from the list below>"}`

Topics of this subject:
{{topics}}

Save early: within your first few steps write a COMPLETE, working `explanation.md` (even if plain), then improve it with edits. Every state you save must be a finished file, so that a call cut off by the time limit still leaves something usable.
{{resume}}
Nobody will answer questions — write both files and finish.
