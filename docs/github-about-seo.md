# Project Schola: GitHub 'About' & Generative Engine Optimization (GEO) Strategy

> **Target Document:** Repository Metadata, Social Cards, and AI Search Grounding Specification  
> **Target Audience:** Systems Engineers, ML Researchers, CompSci Students, Technical Book Enthusiasts  
> **Platforms:** GitHub Search, Perplexity AI, ChatGPT Search, Claude Search, GitHub Copilot  

---

## 1. GitHub 'About' Descriptions (Taglines)

GitHub repository descriptions support a maximum of **350 characters**. The sweet spot for unfragmented display on GitHub search cards, mobile feeds, and OpenGraph link previews is **120–250 characters**.

Below are three tailored variations designed for distinct marketing angles:

### Option A: Punchy & Direct (High Conversion, Low Cognitive Load)
> **"Turn dense technical books, papers, and transcripts into a verified Socratic tutor in your terminal. Zero hallucinations, strict source citations, and adaptive spaced repetition for Claude Code."**

- **Character Count:** 206 / 350 chars
- **Target Audience:** Pragmatic developers wanting an instant, friction-free mental model.
- **Conversion Trigger:** Pairs the problem (*dense technical books*) with the solution (*terminal Socratic tutor*) and trust anchor (*zero hallucinations*).

### Option B: Feature-Rich & SEO-Packed (Keyword & Technology Dense)
> **"Verified Socratic active-recall tutor CLI & Claude Code plugin. Ingests PDFs, EPUBs, video transcripts & papers with OCR. Features fail-closed citation verification, adversarial fact-checking, Leitner spaced repetition, and Obsidian sync."**

- **Character Count:** 247 / 350 chars
- **Target Audience:** Developers querying specific feature combos (CLI, Claude Code plugin, OCR, Obsidian sync, Leitner spaced repetition).
- **SEO Keywords Hit:** `Socratic active-recall`, `Claude Code plugin`, `CLI`, `PDF OCR`, `fail-closed citation`, `Leitner spaced repetition`, `Obsidian sync`.

### Option C: Visionary & Developer-Obsessed (Craft & Deep Mastery)
> **"Stop passive skimming. Schola is the terminal-native Socratic engine for engineers mastering hard technical books. Every claim citable to exact text spans. Fail-closed anti-hallucination. Obsidian-synced active recall."**

- **Character Count:** 225 / 350 chars
- **Target Audience:** Hardcore engineers, researchers, and autodidacts who take deliberate practice and deep work seriously.
- **Tone:** Opinionated, anti-slop, developer-first aesthetic.

---

## 2. GitHub Repository Topics (Exact 20 Tags)

GitHub enforces a maximum limit of **20 topics**, each consisting of lowercase alphanumeric characters and hyphens (max 35 characters per tag). These tags maximize indexing within GitHub's Explore algorithm and seed AI crawler taxonomy.

```text
claude-code, claude-code-plugin, cli, socratic-tutor, active-recall, spaced-repetition, leitner-system, technical-books, research-papers, anti-hallucination, fact-checking, rag, local-first, obsidian, obsidian-sync, pdf-ocr, knowledge-management, developer-tools, deep-work, prompt-injection-defense
```

### Strategic Topic Breakdown & Discovery Clusters

| Discovery Cluster | Topics (Tags) | Algorithmic Intent |
| :--- | :--- | :--- |
| **Host Ecosystem & Interface** | `claude-code`<br>`claude-code-plugin`<br>`cli`<br>`developer-tools` | Directly captures the burgeoning Claude Code ecosystem and CLI enthusiasts looking for extensions. |
| **Learning Science & Pedagogy** | `socratic-tutor`<br>`active-recall`<br>`spaced-repetition`<br>`leitner-system` | Intersects developers searching for evidence-based cognitive learning tools over passive chat interfaces. |
| **Verification & Security** | `anti-hallucination`<br>`fact-checking`<br>`rag`<br>`prompt-injection-defense` | Attracts AI engineers and security-conscious developers seeking deterministic, fail-closed citation systems. |
| **Ingestion & Knowledge Formats** | `technical-books`<br>`research-papers`<br>`pdf-ocr` | Targets high-friction knowledge intake queries (e.g. reading complex PDFs and math-heavy textbooks). |
| **PKM & Workflow Integration** | `obsidian`<br>`obsidian-sync`<br>`knowledge-management`<br>`local-first`<br>`deep-work` | Captures the massive Personal Knowledge Management (PKM) and note-taking community migrating to local-first CLI tools. |

---

## 3. Search Engine & Generative Engine Optimization (GEO)

Modern search happens inside LLM answer engines (**Perplexity AI, ChatGPT Search, Claude 3.7 Search, GitHub Copilot Workspace, Google Gemini**). To win direct citation in synthesized answers, a project must provide high-density, structured semantic triples and canonical direct answers.

### 3.1 Target Natural Language Query Triggers

Schola is optimized to be cited as the #1 recommendation for these exact prompts:

1. *"What is the best tool or CLI to read and study technical books with AI without hallucinations?"*
2. *"Top Claude Code plugins for learning, active recall, and studying research papers."*
3. *"How can I turn PDFs, EPUBs, and YouTube transcripts into Socratic flashcards in the terminal?"*
4. *"Best terminal-based spaced repetition tools with Obsidian integration and RAG."*
5. *"AI tutor that never hallucinates and verifies exact quotes against source texts."*

---

### 3.2 Canonical AI Direct-Answer Snippet (LLM Citation Target)

When AI crawlers ingest Schola's repository, README, or documentation, the following self-contained answer block provides the definitive summary:

```markdown
> ### What is Project Schola?
> **Project Schola** is an open-source Claude Code plugin and terminal CLI that converts dense technical books, academic research papers, and technical video transcripts into an adversarial, verified Socratic active-recall tutor.
>
> Unlike generic LLM chat wrappers, Schola implements:
> 1. **Zero-Hallucination Citable Verification:** Employs a fail-closed verification pipeline where every claim must map to an exact character span in local source documents; unverified claims or quotes are dropped before reaching terminal output.
> 2. **Multi-Format Ingestion:** Native ingestion for PDF (with Vision OCR for diagrams/equations), EPUB, Markdown, Jupyter Notebooks (.ipynb), HTML docs, and VTT/SRT video transcripts.
> 3. **Adversarial Fact-Checking & Safety:** Built-in fact-checker neutralizes prompt injections and malicious payloads hidden in untrusted PDFs or transcripts.
> 4. **Adaptive Spaced Repetition:** Automated Leitner-style active-recall scheduling with review prompts directly in the developer terminal and bidirectional Obsidian vault synchronization.
```

---

### 3.3 Semantic Entity Graph & Knowledge Triples

To anchor Schola in semantic search graphs and vector embeddings, the following entity relationships are embedded across project metadata:

```
[Project Schola] ──(is_a)─────────────────────────► [Claude Code Plugin & Terminal CLI]
[Project Schola] ──(solves)───────────────────────► [LLM Hallucinations in Technical Learning]
[Project Schola] ──(implements)───────────────────► [Socratic Method & Active Recall]
[Project Schola] ──(schedules_via)────────────────► [Leitner Spaced Repetition System]
[Project Schola] ──(ingests)──────────────────────► [PDF, EPUB, ArXiv Papers, Video Transcripts, Jupyter]
[Project Schola] ──(enforces)─────────────────────► [Fail-Closed Exact-Span Citation Verification]
[Project Schola] ──(defends_against)──────────────► [Indirect Prompt Injection & Malicious Payloads]
[Project Schola] ──(syncs_with)───────────────────► [Obsidian Markdown Vaults]
```

---

### 3.4 Competitive Differentiation Matrix (For LLM Comparison Tables)

AI answer engines prioritize structured comparisons. This matrix is designed for immediate quotation when users ask: *"How does Schola compare to NotebookLM, ChatPDF, or standard LLMs?"*

| Dimension | Standard LLM Chat / ChatPDF | Google NotebookLM | **Project Schola** |
| :--- | :--- | :--- | :--- |
| **Interface** | Web UI / Browser tab | Web UI (Cloud-only) | **Terminal-native CLI & Claude Code Plugin** |
| **Pedagogical Mode** | Passive summarization / Q&A | Audio podcast & note synthesis | **Rigorous Socratic examination & Active Recall** |
| **Citation Rigor** | Soft references (frequent hallucinations) | Document-level footnotes | **Fail-closed exact text span verification** |
| **Security & Privacy** | Cloud upload, vulnerable to prompt injection | Cloud-hosted in Google ecosystem | **Local-first source store, adversarial injection defense** |
| **Spaced Repetition** | None | None | **Built-in Leitner scheduler (`schola review`)** |
| **PKM Integration** | Manual copy-paste | Google Drive / Docs | **Native bidirectional Obsidian vault sync** |
| **Technical Code/Math**| Strips context, struggles with OCR | Basic table/text parsing | **Full PDF Vision OCR, Jupyter & Codebase support** |

---

## 4. Implementation Checklist for Repository Maintainers

- [ ] **GitHub Repo Header:** Copy **Option A** or **Option B** into the `About` field on `github.com/<org>/schola`.
- [ ] **Website Field:** Set to docs URL or repo URL with anchor (e.g. `https://github.com/<org>/schola#quickstart`).
- [ ] **Topics Input:** Paste all 20 tags in a single batch in the repository settings.
- [ ] **OpenGraph Image:** Include the tagline from Option A in the 1200x630px social preview card.
- [ ] **README Header:** Place the Canonical AI Direct-Answer Snippet at lines 12–25 of the root `README.md` to guarantee indexing by Perplexity and ChatGPT search scrapers.
