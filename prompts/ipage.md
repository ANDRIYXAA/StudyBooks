A student uploaded an interactive web page for their study book. It was often made by an AI chat app on a phone (an artifact) and was never meant to run inside this site. Look at it, make it work here, and say where it belongs.

Files in your working folder:
- `page.html`: the page. Edit it in place. Every state you save must be a complete working file. When the upload is React/JSX source (`original.jsx` or `original.tsx` and no `page.html` yet), write `page.html` fresh as ONE plain HTML + JavaScript file with the same behaviour, the same controls and the same look (no React, no JSX, no build step).
- `original.*`: the untouched upload. Read only, never edit it.

The student's file name: {{filename}}
Subject the student chose: {{subject_hint}}
Problems the automatic checker found. Fix every one:
{{problems}}
{{round_note}}

Where the page is served: in a sandbox with no network at all, with only inline scripts and styles. So:
- One self-contained file that works offline. No CDN, no `<script src>`, no `<link>`, no `@import`, no web fonts, no images from outside (small `data:image/…` pictures are fine). If the page used a library (Chart.js, D3, React, Tailwind, Three.js, MathJax, KaTeX, …) replace it with plain JavaScript, SVG or canvas and hand-written CSS that does the same job and looks the same. Math as readable Unicode/HTML.
- No network calls of any kind (`fetch`, XMLHttpRequest, WebSocket, …). No calls to an AI or other API (for example `window.claude`, `window.storage`). No `localStorage`, `sessionStorage`, cookies or `indexedDB`: keep state in variables. If a feature depends on one of these, replace it with a local equivalent, or remove it and write that in `changes`.
- No links to other pages, no navigation, no forms, iframes, `window.open`, `eval`, `new Function`, `<meta http-equiv>`. `<meta charset>` and `<meta name="viewport">` are fine.
- It must fit a 360 px wide phone screen without horizontal scrolling, and work on a laptop. Keep or add light and dark mode via `prefers-color-scheme`.

What to do:
1. Read the whole page. Work out what it teaches and how the student uses it.
2. Fix every rule break above with the smallest change that keeps the page's purpose, controls and look.
3. Read the code for bugs: errors, controls that do nothing, NaN or division by zero, handlers on elements that do not exist, text or drawings that overflow a phone screen. Fix them.
4. Check the physics, the maths and the units. If a formula or number is wrong, correct it and say so in `changes`.
5. Do not add features and do not translate: keep the page's own language, layout and controls. Bring the look (colours, fonts, surfaces, buttons, plot colours) to the house style in the skill `studybooks-interactive`, so that the page looks like the Studybooks site.
6. Decide where it belongs in the book. Subjects and topics:
{{syllabus}}

Then write `result.json`:
`{"subject": "<subject id from the list above; the student's choice when they made one>", "topic": "<topic id from the list, or a new short lowercase-hyphen id>", "topic_title": "<English title for a NEW topic, otherwise empty>", "title": "<short English title of the page>", "changes": ["<one short English line for each thing you changed or removed>"]}`
The title is the label on the page's button, so it must say what the page does in plain words and be at most 70 characters (about 10 words): no filler like "Interactive" or "Explorer" unless it is needed. Valid subject ids: {{subjects}}. If you changed nothing, `changes` is `[]`.

Save early: within your first steps `page.html` must be a complete, working file, then improve it with edits.
Nobody will answer questions. Write both files and finish.
