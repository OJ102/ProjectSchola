# Project Schola: GitHub About and search metadata

The live GitHub metadata for [OJ102/ProjectSchola](https://github.com/OJ102/ProjectSchola), plus the wording rules behind it. Keep this file, the GitHub About field and the top of `README.md` in step.

## 1. About description (live)

> Socratic AI tutor for Claude Code. Learn any topic, or teach from your own PDFs, textbooks, docs and videos, with verified citations, quizzes, spaced review and live Obsidian notes. Local-first.

- 194 characters. GitHub allows 350; 120–250 display without truncation on search cards and link previews.
- It names the product type first (Socratic AI tutor, Claude Code), then the sources, then the features people search for.

## 2. Topics (live, 19)

```text
active-recall, anti-hallucination, claude-code, claude-code-plugin, cli, deep-work, developer-tools, fact-checking, knowledge-management, leitner-system, local-first, obsidian, obsidian-sync, pdf-ocr, prompt-injection-defense, research-papers, socratic-tutor, spaced-repetition, technical-books
```

| Cluster | Topics |
| :--- | :--- |
| Host | `claude-code`, `claude-code-plugin`, `cli`, `developer-tools` |
| Learning | `socratic-tutor`, `active-recall`, `spaced-repetition`, `leitner-system` |
| Verification and safety | `anti-hallucination`, `fact-checking`, `prompt-injection-defense` |
| Sources | `technical-books`, `research-papers`, `pdf-ocr` |
| Notes and workflow | `obsidian`, `obsidian-sync`, `knowledge-management`, `local-first`, `deep-work` |

`rag` was removed: Schola does not use embeddings or vector retrieval. Candidates to review: `leitner-system` (the scheduler uses fixed expanding intervals, 1–60 days, not Leitner boxes) and `cli` (Schola runs inside Claude Code; it has no standalone CLI). One free slot remains; `ai-tutor` is a good fit.

## 3. Canonical definition (matches the README)

> **Project Schola** is an open-source Claude Code plugin that turns Claude into a Socratic private tutor. It finds the edge of what you already know, proposes a lesson plan as a dependency map, then teaches one idea at a time and checks each with a multiple-choice quiz. It can also ingest a PDF, EPUB, web page, slide deck or video transcript into a local store and teach from that source's own outline, with every quote checked against the source text and page numbers rendered by code. Session notes sync live to your Obsidian vault.

Search queries this should answer:

1. "AI tutor that teaches from my own PDF or textbook"
2. "Claude Code plugin for learning or studying"
3. "Socratic tutor with citations to the source"
4. "Spaced repetition from a book, with Obsidian notes"
5. "Learn from a YouTube video or docs site with an AI tutor"

## 4. Wording rules

Every public claim must be true of the code. Use these, not the overclaims:

| Say | Don't say | Why |
| :--- | :--- | :--- |
| verified citations; every quote checked against the source | zero hallucinations; never hallucinates | The check proves a quote exists in the source, not that the claim follows from it. |
| page numbers rendered by code | the model cites pages | Locators come from the store's page labels. |
| a Stop hook checks the tutor's cites in the session log | unverified claims never reach the terminal | The check rewrites the Obsidian log; the live reply is not filtered. |
| live Obsidian notes; one-way mirror | bidirectional Obsidian sync | Notes are written to the vault, never read back. |
| spaced review at 1, 3, 7, 14, 30 and 60 days | Leitner scheduler; `schola review` command | Fixed expanding intervals; reviews run through `/teach --continue <slug>`. |
| runs inside Claude Code | terminal CLI | There is no standalone binary. |
| figures described lazily when a unit is taught | Vision OCR for diagrams and equations | OCR is for text pages; figures get Claude vision on demand. |
| local-first: sources stay on your machine | nothing leaves your machine | Brief text is sent to Claude while you are taught. |

Comparisons with other products (NotebookLM, ChatPDF and similar) stay out of public copy unless each claim about them is checked against their current docs.

## 5. Maintainer checklist

- [x] About description set (section 1).
- [x] Topics set (section 2); `rag` removed.
- [x] README opens with the canonical definition (section 3).
- [ ] Optional: add `ai-tutor`; review `leitner-system` and `cli`.
- [ ] Optional: a 1200×630 social preview image with the About line.
