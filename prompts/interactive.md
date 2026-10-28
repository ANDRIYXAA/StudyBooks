A 1st-semester B.Sc. Aerospace student at TUM asks for an interactive page for their course **{{subject}}**.

Their request: {{request}}
Topic: {{topic}}

The current textbook entries of this subject are in `context/<topic>/<entry>.md` — read the ones that matter, so the page uses the same notation, symbols and conventions as the student's material. Reference textbooks are in {{refs}} (`<subject>/<book>/p0123.txt` = page 123):
{{refs_list}}

Write `page.html`: ONE self-contained file that works offline by double-click.
- No external resources at all: no CDN, no web fonts, no images from the web. Inline CSS and plain JavaScript; draw with SVG or canvas; math as readable Unicode/HTML (no MathJax/KaTeX).
- `<!DOCTYPE html>`, `<meta charset="utf-8">`, a viewport meta tag; works on a phone (no horizontal scrolling at 360 px width) and on a laptop; supports light and dark mode via `prefers-color-scheme`.
- The point is to explore: sliders/inputs for the parameters, a live diagram or plot that updates immediately, the key numbers shown with units, a short explanation of what the student sees and what to try ("set α to 0 and watch…").
- The physics/maths must be correct; verify formulas against the references when they cover it. Show the formula used on the page.
- English text, no emoji.

Then write `result.json`: `{"title": "<short English title>", "topic": "<topic id from the list below>"}` The title is the label on the page's button, so it must say what the page does in plain words and be at most 70 characters (about 10 words): no filler like "Interactive" or "Explorer" unless it is needed.

Topics of this subject:
{{topics}}

Save early: within your first few steps write a COMPLETE, working `page.html` (even if plain), then improve it with edits. Every state you save must be a finished file, so that a call cut off by the time limit still leaves something usable.
{{resume}}
Nobody will answer questions — write both files and finish.
