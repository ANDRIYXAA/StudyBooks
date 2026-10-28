You are looking up the reference textbooks for a student's lecture material, so that another step can check and correct it. Work only inside the current directory, plus read-only access to the textbooks listed below. Never ask questions, never stop half-way: there is nobody to answer. Write the one file described below and finish.

## Input

The transcribed entries (in their original language) in the current directory:
{{entries}}

Reference textbooks. `<book dir>/p0123.txt` is the text of page 123. The map of a book lists its sections with their pages and key terms:
{{books}}

## Task

1. Read the entries and note the definitions, theorems, formulas, methods and standard results they use.
2. For each of them, find the place in the textbooks: read the map of the book first and pick the pages from it. Then Read only those pages — at most about 12 pages in total. Use Grep on a book's page files only when its map does not show where a term is. Never read a book page by page.
3. Write `refs_notes.md`:

```
# e1
- **<term or result>**: <the exact statement from the book, formulas as LaTeX $...$> (<book title> p.<page>)
- ...
# e2
...
```

   - Quote the book's statement with all its conditions; do not paraphrase a formula.
   - Only what the entry really uses; skip what the books do not have. When nothing fits an entry, write `- nothing found` under it.
   - At most about 40 lines per entry.
