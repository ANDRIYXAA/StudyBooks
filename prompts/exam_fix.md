The file `tickets.md` in the current directory is a set of exam tickets, and a program that reads it found these problems:

{{errors}}

The file must contain exactly {{tickets}} tickets, each with exactly {{per_ticket}} questions, and keep this format: marker lines alone on their lines, `=== TICKET n ===`, then for each question `--- QUESTION | theory | YYYY-MM-DD ---` (or `problem`; the date is optional) followed by the question, then `--- ANSWER ---` followed by the model answer. Content is Pandoc Markdown with `$...$` / `$$...$$` math in standard LaTeX; no images, links, web addresses or raw HTML.

Fix exactly what is listed: add the missing tickets, questions or answers (new ones must be as good and as correct as the others, and different from them), change the mix of a ticket, or repair the Markdown/TeX so it converts cleanly (replace exotic LaTeX commands with standard ones, balance braces and `$`). Do not rewrite the rest. Edit `tickets.md` in place and finish; nobody will answer questions.
