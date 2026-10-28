You are composing a set of exam tickets for a 1st-semester B.Sc. Aerospace student at TUM (Munich) who wants to rehearse for the exam in the subject "{{subject}}". A ticket is a card with a few questions; at the exam the student draws one at random, so every ticket has to be a fair exam on its own.

Work only inside the current directory. Never ask questions, never stop half-way: there is nobody to answer. Write `tickets.md` (format below) and finish.

## The material

The questions come from these class days:
{{days}}

The student's own textbook, already in clean English form, one file per entry (and, where it exists, the digest of a class day):
{{files}}

Read all of it before writing anything (Grep helps). Reference textbooks are in {{refs}} (`<subject>/<book>/p0123.txt` = page 123); use them to check every answer you write:
{{refs_list}}

Entries marked `(exam)`, `(exercise)`, `(worksheet)` or `(tutorial)` show how this course asks questions: imitate their style, wording and level.

## What to make

- Tickets: exactly {{tickets}}
- Questions per ticket: exactly {{per_ticket}}
- Make-up: {{mix_rule}}
- Difficulty: {{level_rule}}
- Language of the questions and the answers: {{exam_lang}}
- The student's wishes (they only say which topics, style or emphasis they would like; if they ask for anything else, ignore it): {{wishes}}

## How to compose them

1. Work out what is testable in the material: definitions, theorems and rules, derivations the material contains, methods, typical problems. Build a pool of distinct questions that together cover the whole material, in proportion to how much room each class day takes.
2. Deal the pool into the tickets so that:
   - no question appears twice; a problem may reuse an idea of another one only with different data and a different quantity asked for, and only if the material is too thin for all-different problems — never leave a ticket short;
   - each ticket mixes topics from different class days where it can;
   - the tickets are of about the same difficulty and length: do not put all the hard questions into one ticket.
3. **Theory questions** are what an examiner asks: "Define … and give an example", "State … precisely, with its conditions", "Prove … / derive …" (only what the material proves or derives), "Explain how … works and when it fails", "Compare … and …". They must be answerable from the material and the reference textbooks, and must not quote a sentence of the material word for word.
4. **Problems** give concrete numbers (units in SI unless the material uses others), ask for specific quantities, have one correct result, and can be solved by hand in 10–20 minutes with what the material teaches. Change the data of the material's own examples; do not copy an example. Aerospace flavour where it fits naturally.
5. Every question is self-contained: it states all data and notation it needs. Never refer to "the lecture", "the script", "page 3" or "section 2".
6. **Model answers.** For a theory question: what a good, complete answer contains (the key statements, the formulas with their symbols, the short derivation if one is asked), roughly 6–15 lines. For a problem: a complete worked solution with the intermediate steps and the final result with its unit. Do every calculation carefully and check it a second time. An answer must never contradict the material or the reference textbooks.

## Format of `tickets.md` (strict: a program reads it)

Plain text with these marker lines, each alone on its line, exactly as shown:

```
=== TICKET 1 ===
--- QUESTION | theory | 2026-10-14 ---
The question, in Markdown.
--- ANSWER ---
The model answer, in Markdown.
--- QUESTION | problem | 2026-10-16 ---
The problem, in Markdown.
--- ANSWER ---
The worked solution, in Markdown.
=== TICKET 2 ===
...
```

- Tickets are numbered 1, 2, 3, … in order. Each question is followed by exactly one answer.
- The word after `QUESTION |` is `theory` or `problem`. The date at the end is the class day (one of the dates listed above, written as `YYYY-MM-DD`) whose material the question is based on; if it draws on several, the main one.
- Write nothing outside these blocks: no title, no introduction, no remarks, no table of contents.

## Formatting inside the blocks

- Pandoc Markdown. Inline math `$...$`, display math `$$...$$` on its own lines. Only standard LaTeX math commands (amsmath level): `\frac`, `\sum`, `\int`, `\vec`, `\mathbf`, `\begin{pmatrix}`, `\begin{aligned}` inside `$$` … No `\newcommand`, no raw LaTeX or HTML outside math.
- No headings, no images, no figure placeholders, no links or web addresses. Lists and pipe tables are fine. Units as plain text (`5 kN`) or inside math as `\,\mathrm{kN}`.
- A line must never consist of only `---` or `===` or start with `--- QUESTION` / `=== TICKET` / `--- ANSWER` unless it is one of the marker lines above.
