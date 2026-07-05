# PDF Accessibility Studio

> **AI Builders Challenge — July 2026 · IBM Bob**
> **Theme: Design / UX / Visual Communication — AI-Powered Design & Visual Concept Tools**

Designers spend hours crafting visual documents — structured layouts, clear hierarchies, tables, imagery. But when those designs are exported as PDFs, their visual language becomes invisible to millions of readers who rely on screen readers. **PDF Accessibility Studio is an AI creative partner that reads your design, understands its visual intent, and extends it to every audience — automatically.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![PDF/UA-1](https://img.shields.io/badge/standard-PDF%2FUA--1-green)](https://www.pdfa.org/resource/pdfua-1/)
[![WCAG 2.2 AA](https://img.shields.io/badge/standard-WCAG%202.2%20AA-green)](https://www.w3.org/TR/WCAG22/)
[![Built with IBM Bob](https://img.shields.io/badge/built%20with-IBM%20Bob-0f62fe)](http://ibm.biz/university-bob)

---

## 🎯 Problem Statement

Designers spend hours crafting beautiful, structured visual documents. But the moment a PDF is exported, its visual language — the hierarchy, the reading order, the structure of every table and image — becomes invisible to a quarter of its potential audience.

> **Accessibility is not a compliance checkbox bolted on after the design is done — it is an inseparable dimension of good visual communication. A document's heading structure, reading order, table layout, and image descriptions are design decisions. When they are missing, the design is incomplete.**

Over **2.5 billion PDFs** are published every year. The vast majority are visually designed first — exported from InDesign, Word, or Figma — with rich visual hierarchy but no semantic structure for assistive technology. For the **285 million people** worldwide with visual impairments, and millions more with cognitive or motor disabilities, a JAWS or NVDA screen reader navigating an untagged PDF hears: *"blank… blank… blank."* The structured authoring intent — every heading level, every table column, every image caption — never reaches the reader.

### Why most PDFs are inaccessible

The root cause is a **design-to-export gap**. PDFs fail accessibility for four compounding reasons:

| Root Cause | What goes wrong |
|---|---|
| **Visual-first authoring tools** | InDesign, Word, PowerPoint, and Figma export visual appearance — fonts, positions, colours — but discard semantic meaning. There is no heading tag, no reading order, no table structure in the export. The PDF looks right. It reads as nothing. |
| **No tagging by default** | PDF creation tools do not generate a tag tree unless explicitly configured. Most organisations never configure it. The result is a flat stream of positioned text with no structure a screen reader can traverse. |
| **Scanned and legacy documents** | Millions of PDFs are scanned images — government forms, legal contracts, academic papers, archival records. They contain zero extractable text. OCR alone does not solve this; semantic structure must be reconstructed from scratch. |
| **Complexity of the standard** | PDF/UA-1 (ISO 14289-1) requires not just tags, but correct heading hierarchies, table header associations (`scope`, `id`, `headers`), artifact markers for decorative elements, and document language declarations. Getting all of these right simultaneously requires specialist knowledge most teams do not have. |

Manual remediation by an accessibility consultant costs **$3–$15 per page** and requires specialist knowledge of PDF/UA structure trees, WCAG 2.2, and Section 508 tagging rules. For a 200-page archive, that's $600–$3,000 — per document. The result: organisations skip remediation, creators cannot afford to fix legacy archives, and accessibility is treated as an afterthought rather than a creative baseline.

**The visual design is complete. The audience isn't. PDF Accessibility Studio closes that gap — with AI, automatically, for every document.**

---

## 🎨 Selected Challenge Theme

**Theme: Design / UX / Visual Communication**
**Focus area: AI-Powered Design and Visual Concept Tools**

This project sits at the intersection of visual design and AI. The challenge asks: *How can AI act as a creative partner rather than simply a content generator?* PDF Accessibility Studio answers that directly.

| Challenge Question | How this project answers it |
|---|---|
| **How can AI help people create faster?** | A document that takes an accessibility consultant 2–4 hours to remediate manually is processed in under a minute — with no human input required. |
| **How can AI act as a creative partner?** | The AI doesn't replace the designer's visual choices. It analyses the visual layout — columns, heading hierarchy, separator lines, tables, images — and translates that design intent into semantic structure. It preserves the creator's visual language and extends it to every reader. |
| **How can AI bridge the gap between imagination and execution?** | Designers imagine documents as visual experiences. The AI bridges that vision to the structured, tagged reality that screen readers, RAG pipelines, and AI search engines need — without the designer needing to know anything about PDF/UA or WCAG. |
| **How can AI unlock entirely new creative experiences?** | Retroactive accessibility at scale was previously impossible without specialist tools costing thousands per licence. This makes it available to any creator, for any document, instantly. |

Accessibility *is* a design discipline. A document that cannot be read by all of its intended audience is an incomplete design. This tool completes it.

---

## 💡 Solution Description

Upload any PDF — scanned, image-only, legacy export, or poorly structured. The system runs it through a **9-stage AI pipeline** and returns:

- A fully tagged, accessible PDF with PDF/UA-1 and WCAG 2.2 AA compliance
- AI-derived document metadata (title, subject, keywords, language)
- A navigable bookmark outline written to the PDF navigation panel
- An accessibility score (0–100) with a per-check audit report
- A QA corrections log showing exactly what was fixed

> **Structured authoring, UX best practices, and visual communication design are the foundation of every stage — the AI reads column layout and reading order as a UX problem, interprets heading hierarchy as information architecture, and writes alt-text by understanding the visual context a sighted user experiences.**

No accessibility consultant. No manual tagging. No specialist knowledge required.

**Before → After:**

| Before | After |
|---|---|
| Untagged, screen readers read nothing | Fully tagged, logical reading order |
| No document title or language | AI-derived title, subject, keywords, language set |
| Images with no alt-text | Context-aware alt-text from surrounding paragraphs |
| Tables with no header associations | Full TH/TD/scope/id/headers compliance |
| Decorative lines read as "blank" | Separator artifacts — silently skipped |
| No navigation bookmarks | H1–H6 bookmark outline in PDF viewer panel |

### Why this solution is unique

Every other PDF accessibility tool on the market fails in at least one of the four root causes above. PDF Accessibility Studio is the only system that addresses all of them simultaneously, end-to-end, with no human in the loop.

| Comparison | Commercial tools (Adobe Acrobat Pro, CommonLook, axesPDF) | PDF Accessibility Studio |
|---|---|---|
| **Approach** | Human operator tags manually, stage by stage | Fully autonomous — AI runs all 9 stages without a human operator |
| **Scanned / image PDFs** | Require separate OCR pre-processing, often fail | Multimodal vision pass reconstructs text and layout from the raw page image |
| **Table compliance** | Surface-level — adds TH tags, misses scope/headers/colspan/rowspan | 7-rule cell integrity: scope, id, headers, empty cells, merged cells, split headers, complex Summary |
| **Alt-text** | Generic or left blank | Context-aware — reads surrounding paragraph, caption, and heading before writing; prohibits generic strings |
| **Separator handling** | Often tagged as `<P>` (causes "blank" announcements) | Always tagged `<Artifact Type=Layout Subtype=Separator>` — silently skipped by every conforming screen reader |
| **Cost** | $3–$15 per page, thousands per software licence | Cost of a few API calls per document |
| **Self-auditing** | No | LLM audits its own tag tree against 7 PDF/UA + WCAG rules after every page |
| **Error recovery** | Manual retry | Exponential backoff + deterministic fallback — pipeline never hard-crashes |

> **The demand for this system is structural, not trend-driven.** The EU Accessibility Act came into force in June 2025, extending mandatory digital accessibility requirements across all EU member states. The US Section 508 Refresh and the DOJ's final rule on web accessibility (April 2024) apply to every federal agency and contractor. Organisations worldwide are sitting on archives of inaccessible PDFs with no affordable, automated path to compliance — until now.

---

## 🏗️ AI Approach & Architecture

The pipeline is a **9-stage sequential async LLM prompt chain**. Each stage receives the structured JSON output of the previous stage, enriches it, and passes it forward. A `GlobalContext` object carries heading depth and table continuation state across pages.

```
User (browser)
     │
     ▼
Streamlit UI  ·  FastAPI REST + SSE  (app.py)
     │  file upload / live stage progress / download / audit report
     ▼
9-Stage AI Pipeline  (ai_pipeline.py)  ── async, sequential per page
     │
     ├─ Stage 0   · OCR Extraction        — multimodal vision, token-level bounding boxes
     ├─ Stage 1   · Layout Analysis       — zone classification: Header/Para/Heading/Table/Image/Separator
     ├─ Stage 2   · Unicode Repair        — text stitching, encoding correction, hyphenation
     ├─ Stage 3   · Tag Tree Assembly     — semantic PDF tag tree + artifact rules
     ├─ Stage 3.5 · Metadata & Bookmarks  — AI-derived title/keywords + H1–H6 outline
     ├─ Stage 4   · Image Alt-Text        — context-aware alt-text from surrounding text
     ├─ Stage 5   · Table Parsing         — full TH/TD/scope/id/headers/colspan/rowspan
     ├─ Stage 6   · Contact Link Detection — mailto/tel/href tagging with E.164 normalisation
     └─ Stage 7   · Compliance QA         — 7-rule audit with auto-correction
          │
          ▼
     pdf_processor.py  ── PyMuPDF StructTreeRoot + ParentTree binary injection
          │
          ▼
     Accessible PDF output  +  Accessibility Score  +  Audit Report
```

### Supporting modules

| Module | Role |
|---|---|
| `ai_pipeline.py` | Async 7-prompt chain, retry with exponential backoff, deterministic fallback |
| `pdf_processor.py` | PDF → PIL images (Poppler); TagTree → PDF binary (PyMuPDF StructTreeRoot) |
| `schemas.py` | Pydantic data contracts for every inter-stage boundary |
| `checker.py` | Standalone 10-rule deterministic accessibility checker (no LLM required) |
| `troubleshooter.py` | LLM-backed 2-phase diagnostic advisor — classifies failures, generates resolution plans |
| `app.py` | Streamlit UI + FastAPI REST API + SSE streaming for job progress |
| `tests/test_api.py` | End-to-end API tests covering all endpoints |

### Resilience design
- **Exponential backoff** on every LLM call (up to 4 retries, 30 s max)
- **Deterministic fallback** — if LLM fails, the pipeline derives a best-effort tag tree from OCR tokens alone
- **LLM-backed troubleshooter** — on any pipeline error, a separate advisor LLM classifies the failure (zone / tag / ingestion), generates clarifying questions, and produces a concrete resolution plan
- **Per-page accuracy check** — after each page, the LLM self-audits the tag tree against 7 PDF/UA + WCAG rules

### Accessibility standards targeted

| Standard | Target |
|---|---|
| **PDF/UA-1** (ISO 14289-1) | Full conformance on output documents |
| **WCAG 2.2 Level AA** | All applicable success criteria |
| **Section 508** | US federal electronic document provisions |

### LLM compatibility

The pipeline uses a swappable `LLM_API_URL` environment variable. It works with any OpenAI-compatible endpoint — including **IBM watsonx** via its OpenAI-compatible API and **IBM Granite** models. Set `LLM_API_URL` to your watsonx deployment URL to run the full pipeline on IBM infrastructure.

---

## 🤖 How IBM Bob Was Used

IBM Bob was the **primary development tool** for this entire project — from the first specification to the final line of code. This is not a cosmetic acknowledgement; Bob shaped every architectural decision.

### How Bob was used

**1. Spec-driven development**  
Before a single line of code was written, IBM Bob authored the complete technical specification (`docs/SPECIFICATION.md`), functional requirements (`docs/REQUIREMENTS.md`), and proof-of-concept plan (`docs/PROOF_OF_CONCEPT.md`). The 9-stage pipeline architecture, inter-stage JSON contracts, and artifact tagging rules were all designed in conversation with Bob.

**2. Prompt engineering**  
All 7 LLM prompts — OCR extraction, layout analysis, unicode repair, tag tree assembly, image alt-text, table parsing, and compliance QA — were iteratively refined with Bob. The separator artifact tagging rules (screen readers skip `<Artifact Type=Layout Subtype=Separator>` silently) and the 7-rule table cell integrity system were Bob-designed.

**3. Architecture decisions & debugging**  
Every major architecture decision documented in `docs/PROOF_OF_CONCEPT.md` was validated through Bob-assisted sessions:
- PyMuPDF `set_metadata()` key restriction (only 8 writable keys — passing `doc.metadata` directly causes a `bad dict key(s)` error)
- Stage 3.5 capture pattern (metadata/bookmarks must be extracted outside the main pipeline chain)
- `st.session_state` persistence pattern for Streamlit reruns
- Carbon Design System CSS injection via `st.markdown(unsafe_allow_html=True)`

**4. Full code generation**  
The following modules were generated and refined entirely with IBM Bob:
- `pdf_processor.py` — real PyMuPDF StructTreeRoot + ParentTree binary injection
- `checker.py` — 10-rule deterministic standalone accessibility checker
- `troubleshooter.py` — LLM-backed 2-phase diagnostic advisor
- `schemas.py` — Pydantic data contracts for all 7 inter-stage boundaries
- `tests/test_api.py` — full end-to-end API test suite

**5. Iterative refinement**  
Bob identified and fixed production bugs during development — including the empty table cell retention rule (removing `<TD>` with empty text breaks screen reader column/row announcements) and the separator tagging correctness issue (`<P>` on a separator causes NVDA to announce "blank").

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install streamlit pymupdf httpx pillow pydantic

# 2. Set your OpenAI-compatible API key (never hardcoded)
$env:LLM_API_KEY = "sk-..."          # PowerShell
export LLM_API_KEY="sk-..."          # bash/zsh

# 3. Run the Streamlit UI
python -m streamlit run app.py
```

**Demo mode (no API key needed):**  
The pipeline runs with mocked LLM responses out of the box. Every stage completes, metadata and bookmarks are injected, the accessibility score is computed, and the audit report is displayed. To activate live AI processing, set `LLM_API_KEY`.

**Poppler** (required for PDF-to-image rendering): bundled in `bin/` for Windows. On macOS: `brew install poppler`. On Linux: `sudo apt install poppler-utils`.

---

## 📊 Accessibility Score

Output is scored 0–100 across seven weighted dimensions:

| Check | Weight |
|---|---|
| Tag Tree Complete | 18 |
| Heading Hierarchy | 18 |
| Alt-Text Coverage | 18 |
| Table Headers | 18 |
| Artifact Markers | 12 |
| Contact Links Tagged | 8 |
| Metadata & Bookmarks | 8 |

Score ≥ 90 = **Excellent** · ≥ 75 = **Good** · ≥ 50 = **Needs improvement** · < 50 = **Poor**

---

## 📁 Project Structure

```
app.py                   # Streamlit UI + FastAPI REST API + orchestration
ai_pipeline.py           # Async 9-stage LLM prompt chain
pdf_processor.py         # PDF ↔ image conversion + StructTree binary injection
checker.py               # Standalone deterministic accessibility checker
troubleshooter.py        # LLM-backed diagnostic advisor
schemas.py               # Pydantic data contracts (all inter-stage boundaries)
tests/
  test_api.py            # End-to-end API tests (FastAPI TestClient)
docs/
  README.md              # Detailed technical documentation
  SPECIFICATION.md       # Component contracts, schemas, stage contracts
  REQUIREMENTS.md        # Functional and non-functional requirements
  PROOF_OF_CONCEPT.md    # Validated decisions, limitations, next milestones
.env.example             # Environment variable reference (no secrets)
```

---

## 🌍 Real-World Impact

- **For people using assistive technology:** Screen readers traverse documents in correct logical order. Headings provide navigation landmarks. Tables announce column and row context. Images are described with specific, contextual alt-text.
- **For organisations:** Retroactively remediate legacy document archives at scale. No accessibility consultant required per document. Full audit trail with correction counts.
- **For AI and search systems:** Tagged, structured PDFs are directly parseable by RAG pipelines, document search indexes, and LLM ingestion tools.
- **For designers and creators:** Publish once. Reach everyone.

---

## 📄 Licence

MIT
