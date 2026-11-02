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

If the request is about a derivation, a calculation or an equation from the material (words like "derive", "step by step", "how do we get", "where does this come from", or a formula in the request), use this plan instead of the plan above:
1. **Goal.** Say what we look for and why. Give the start equation and where it comes from (one line).
2. **Steps.** Write one algebra or calculus action in each step. Number the steps. For each step:
   - name the rule (quotient rule, chain rule, product rule, factor out a common term, cancel, substitute, …);
   - write the expression before and after the action as display math;
   - show every intermediate expression, also the "obvious" ones (factoring out, canceling, expanding a bracket).
3. **Traps.** At a sign change, a minus sign moved in front of a fraction, a division by an expression, or a cancelled factor, add one short line that tells why it is correct (for example "the denominator is always positive, so only the numerator can be zero").
4. **Check.** Do a short check: units, a limit case, or a substitution of the result.
5. **Meaning.** Say in one to three sentences what the result means physically or practically.

Use the same notation as the student's material. Also show the steps that the lecture skips. For a derivation, the numerical example and the typical mistakes are optional. Add them only if they help.

Formatting: Pandoc Markdown, math as `$...$` / `$$...$$` with standard LaTeX commands only, no raw LaTeX/HTML outside math, `#` for its sections (no title heading — the title is added automatically).

Then write `result.json`: `{"title": "<short English title>", "topic": "<topic id from the list below>"}`

Topics of this subject:
{{topics}}

Save early: within your first few steps write a COMPLETE, working `explanation.md` (even if plain), then improve it with edits. Every state you save must be a finished file, so that a call cut off by the time limit still leaves something usable.
{{resume}}
Nobody will answer questions — write both files and finish.
