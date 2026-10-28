You are making the map of part of a textbook, "{{title}}", so that later lookups can read only the pages they need instead of the whole book. Work only inside the current directory. Never ask questions, never stop half-way: there is nobody to answer. Write the one file described below and finish.

## Input

Pages {{first}}–{{last}} of the book, as text. Inside each file a line `=== page N ===` starts page N (N is the page file number, use it as the page number):
{{files}}

## Task

Read all the files, then write `map.md`: one line per section or subsection, in page order, covering every page:

```
- p.<first>–<last> · <section number and heading> — <the key terms, definitions, theorems, laws and formulas of those pages, comma-separated, at most about 25 words>
```

- Group consecutive pages of one section into one line; a long section may get one line per subsection.
- Name things as the book names them, so that a search for the term finds the line. Formulas only by name or as short LaTeX, e.g. `$F = ma$`.
- Pages with only exercises, answers, the index or front matter: one short line each, e.g. `- p.210–214 · Exercises for 3.2`.
