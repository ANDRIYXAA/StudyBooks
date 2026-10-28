You are writing the end-of-chapter digest for one chapter of a student's textbook. The student is in the 1st semester of B.Sc. Aerospace at TUM (Munich). The chapter collects everything from one class day, "{{chapter}}", of the subject "{{subject}}"; it may hold a lecture, its exercise sheet and later supplements, possibly on several topics.

Work only inside the current directory. Never ask questions, never stop half-way: there is nobody to answer. Write the two files described below and finish.

## Input

The chapter's sections, in order, already in clean English textbook form:
{{files}}

Read all of them before writing anything.

## 1. `summary.md` — the digest of the chapter

A condensed revision sheet of THIS chapter: what a student must know before an exam, in a fraction of the length of the chapter (aim for roughly 1/5, at most about two pages).

- Only what is in the sections. Do not add new theory, examples or explanations that are not there.
- Merge material that appears in several sections instead of repeating it. Ignore lecture order: group by kind.
- Use these sections in this order, and leave out a section that would be empty:
  - `# Definitions` — each defined notion in one line: **name** — precise definition (with the conditions).
  - `# Results` — theorems, propositions, rules and laws, stated precisely with all their conditions; no proofs.
  - `# Formulas` — the key formulas, each with what its symbols mean and its units where there are units.
  - `# Methods` — step-by-step procedures the chapter teaches (how to compute / prove / solve …), as short numbered lists.
  - `# Watch out` — typical pitfalls and special cases that the sections themselves point out or that are implied by the conditions of the results above.
- Compact bullet lists, no prose paragraphs, no introduction, no closing remarks, no references to "Lecture 1" or "section 2". Do not repeat the chapter title.
- No figure placeholders, no images.

## 2. `glossary.json` — the terms of the chapter

Every technical term that the chapter introduces or defines (not everyday words, not terms that only appear in passing):

```json
[
  {"term": "supremum", "definition": "The least upper bound of a set $A \\subseteq \\mathbb{R}$: an upper bound $s$ of $A$ such that no number smaller than $s$ is an upper bound of $A$."}
]
```

- `term`: in English, lower case unless it is a proper name (e.g. "Archimedean property", "Gaussian elimination"); singular; plain text without math or Markdown — write symbols as Unicode ("ε-neighbourhood").
- `definition`: one or two sentences in English, exact and self-contained, consistent with the chapter; inline math `$...$` allowed, no display math, no Markdown formatting.
- Valid JSON: escape backslashes in the LaTeX (`\\mathbb`), no trailing commas.

## Formatting rules for `summary.md`

- Pandoc Markdown. Inline math `$...$`, display math `$$...$$` on its own lines. Only standard LaTeX math commands (amsmath level): `\frac`, `\sum`, `\int`, `\vec`, `\mathbf`, `\begin{pmatrix}`, `\begin{aligned}` inside `$$` … No `\newcommand`, no raw LaTeX or HTML outside math.
- `#` for the sections above, nothing deeper than `##`.
- Tables as pipe tables. Units as plain text (`5 kN`) or inside math as `\,\mathrm{kN}`.
