# Studybooks

Studybooks turns photos and files of lectures into two PDF books for each subject. A web site (FastAPI) takes the material. A background worker reads it with a language model, sorts it into chapters, and builds the books with pandoc and Typst.

## What it does

- **Two books for each subject.** `clean.pdf` is in English. A model corrects it and completes it with textbooks. It has a summary of each chapter and a glossary. `original.pdf` is a word-for-word transcription in the original language, with the original photos.
- **One chapter for each class day.** A supplement to an old lecture goes into the chapter of that lecture.
- **Examples in containers.** A worked example of a lecture becomes a framed container in the clean book. It has the problem, the diagram, the solution, an explanation, and a check for small errors. The text and the formulas of the example do not change. A model redraws each diagram as a vector drawing. A second call compares the drawing with the photo. If they differ, the book keeps the photo.
- **Links.** A person adds a link to a video or a site. The program reads the title and the picture of the page. A model puts the link in the chapter of its topic. The list of links is under the book.
- **Interactive pages.** A model makes a simulation or a calculator on request. The program runs each page in an isolated container before it saves the page. It moves the sliders, presses the buttons, and gives errors back to the model. A person can change a page, a chapter, or a whole book with a text request. Each change has one step of undo. Pages of one subject can go together in a combined page with a menu.
- **Exam tickets** with model answers, as PDF files, and a random draw of one ticket.
- **One shared book.** All people see the same books. They can suggest additions, and the admin merges them into the main book.
- **Input files:** photos, PDF files (also "as is"), Office files, and HTML files.

## Engines

A person selects the engines to use in the "switch between" box. There are five engines:

| Engine | How it connects |
|---|---|
| Claude Code | The unmodified command-line tool with the own sign-in of the person. |
| OpenRouter | The own API key of the person. Free models can switch by themselves. |
| Codex | The unmodified command-line tool with a ChatGPT sign-in. This engine is for the book owner only. |
| Grok | The own API key of the person (xAI). |
| Gemini | The own API key of the person (Google). |

The selected engines work from left to right. When an engine reaches its limit, it rests, and the same call continues on the next engine. A note tells the new engine which files the work folder already holds. The first engine works again when its limit is back.

## How it works

```
site ─upload─► inbox ─► worker (sb.py) ─► library (git) ─► pandoc + Typst ─► books/<subject>/{clean,original}.pdf
                          one chunk = 12 pages, 3 passes:
                          1 transcription   2 check against textbooks   3 clean English text
```

The program checks the output of the model and does not trust it. Each output goes through `check_md` (pandoc), `validate_change`, the Typst build, and the page run. The program always treats text from people as untrusted.

## Files

| What | Files |
|---|---|
| Back end (queue, engines, book build) | `sb.py`, `engines.py`, `links.py`, `examples.py`, `combine.py`, `layoutfit.py` |
| Web site | `web.py`, `web/index.html` (one page), `web/hub.html` (combined page), `web/glass.js`, `accounts.py` (public accounts) |
| Prompts and skills of the model | `prompts/`, `skills/` |
| Page checks by running the page | `pagecheck/` (jsdom in docker) |
| Tests (offline, with fake engines) | `tests/` |
| Deployment | `deploy/` (systemd), `build_n8n.py` (n8n workflow), `fetch_refs.sh` (open textbooks) |
| Start subjects | `syllabus.default.yaml` |

## Run

You need Linux, Python 3.12, pandoc 3.7, Typst 0.15, LibreOffice (for Office files), Node 22 and docker (for the page checks). You also need at least one engine: the Claude Code CLI, the Codex CLI, or an API key.

```bash
python -m venv .venv
.venv/bin/pip install fastapi uvicorn PyYAML pymupdf pillow pillow_heif python-multipart
(cd pagecheck && npm install --omit=dev) && docker pull node:22-alpine

export SB_DATA=./data SB_ADMIN=<uid-of-the-admin> SB_BASE_URL=http://127.0.0.1:8810
.venv/bin/uvicorn web:app --host 127.0.0.1 --port 8810     # the site
.venv/bin/python sb.py kick                                # the worker: process the queue
```

The main variables are `SB_DATA` (the data), `SB_ADMIN` (the admin of the main book), `SB_MODEL` (the default model), `SB_PUBLIC=1` (a public instance behind a tunnel), and `SB_PAGECHECK=0` (turn off the page run). [docs/DETAILS.md](docs/DETAILS.md) describes the others.

## Tests

The test suites in `tests/test_*.py` run offline. They use fake engines and a separate `SB_DATA`. Run them from the root of a **copy** of the project. Put the code in the copy, keep `tests/fake_*.py` in `tests/`, and put `test_*.py` in the root.

```bash
cp tests/test_*.py .         # in the copy of the project, not in the repository
SB_DATA=/tmp/x SB_CLAUDE=$PWD/tests/fake_claude.py .venv/bin/python test_days.py
node tests/check_page_dict.js        # the page dictionary (uk/en)
```

The tests never call a real model. `test_pagecheck.py` needs docker and `SB_PAGECHECK_DIR=pagecheck`. Without them, it skips.

## More

[docs/DETAILS.md](docs/DETAILS.md) has the architecture, the endpoints, the constants, the protection of the public instance, the limits, the known traps, and a change log by date. It is in Ukrainian.
