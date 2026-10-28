The file `{{file}}` in the current directory does not typeset. It is converted with pandoc (Markdown with `$...$` / `$$...$$` LaTeX math) to Typst, and the Typst compiler reports:

```
{{error}}
```

Fix `{{file}}` so it converts cleanly: usually a LaTeX math command pandoc cannot translate, an unbalanced brace or `$`, raw LaTeX/HTML outside math, or a broken table. Replace exotic commands with standard ones. Change nothing else — not the wording, not the content. Edit the file in place and finish; nobody will answer questions.
