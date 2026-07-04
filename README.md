# PDF Accessibility Studio

An autonomous AI pipeline that converts inaccessible PDFs into fully compliant, tagged PDF/UA and WCAG 2.2 documents — readable by humans, screen readers, and AI engines.

---

## What it does

Upload any PDF — scanned, image-only, poorly structured, or exported from legacy tools. The system returns a tagged, structured, accessible PDF with no manual intervention required.

---

## Architecture

```
User (browser)
     │
     ▼
Streamlit UI (app.py)
     │  file upload / download / progress
     ▼
7-Stage AI Pipeline (async)
     │
     ├─ Stage 0 · OCR Extraction       — token-level bounding box mapping
     ├─ Stage 1 · Layout Analysis      — zone classification + reading order
     ├─ Stage 2 · Unicode Repair       — text stitching, encoding correction
     ├─ Stage 3 · Tag Tree Assembly    — semantic H1–H6, P, L, LI, Link tags
     ├─ Stage 4 · Image Alt-Text       — context-aware alt description generation
     ├─ Stage 5 · Table Parsing        — TH/TD/colspan/rowspan mapping
     └─ Stage 6 · Compliance QA        — WCAG 2.2 + PDF/UA audit and repair
          │
          ▼
    PyMuPDF (pdf_processor.py)
          │  metadata injection + binary recompilation
          ▼
    Accessible PDF output
```

### Key design decisions

| Decision | Rationale |
|---|---|
| Sequential prompt chain | Each stage passes structured JSON forward; no stage starts without verified prior output |
| Global context tracker | Carries heading depth and table continuation state across pages |
| Async LLM calls | Non-blocking pipeline; UI remains live during processing |
| PyMuPDF for output | Direct binary injection — no conversion artefacts, preserves original fonts and layout |
| Streamlit session state | Results persist across browser reruns; download button never disappears |

---

## Pipeline process

### Stage 0 — OCR Extraction
Multi-modal vision pass over the raw page image. Extracts every token with normalised bounding coordinates (0–1000 scale). Reconstructs distorted, vertical, or broken-encoding characters contextually.

### Stage 1 — Layout Analysis
Maps physical zones across the page: Header, Footer, Paragraph, Heading (1–6), List, Table, Image, Sidebar, Decorative. Enforces correct column reading order — first column top-to-bottom before second column begins.

### Stage 2 — Unicode Repair
Stitches text fragments broken across columns or line-wrapped hyphenations. Repairs corrupted unicode so output is fully searchable and TTS-compatible.

### Stage 3 — Tag Tree Assembly
Builds a valid PDF tag tree from the ordered zone content. Enforces heading level continuity across pages (no skipped levels). Marks running headers/footers as `<Artifact>` so assistive tools skip them.

### Stage 4 — Image Alt-Text
Generates context-aware alternative text for each non-decorative image. Summarises chart data trends within the description. Marks spacers and decorative graphics as `Artifact=True`.

### Stage 5 — Table Parsing
Detects multi-page table continuations and preserves column configurations. Maps all `<TH>` headers with scope declarations. Handles colspan and rowspan for merged cells.

### Stage 6 — Compliance QA
Final audit pass against WCAG 2.2 and PDF/UA. Checks: empty tags, missing alt-text, absent table headers, fractured heading sequence. Outputs the corrected, finalised JSON tag structure.

---

## Accessibility standards

### PDF/UA (ISO 14289)
The output conforms to PDF/UA-1, the international standard for universally accessible PDF. Requirements addressed:

- Logical reading order defined in the tag tree
- All headings tagged H1–H6 with no level skips
- Tables include header cells with scope attributes
- Images carry descriptive alt-text or are marked as artefacts
- Language is set in document metadata (`en-US`)
- Document title present in XMP metadata

### WCAG 2.2 (W3C)
Pipeline addresses the following success criteria:

| Criterion | Level | How addressed |
|---|---|---|
| 1.1.1 Non-text content | A | Alt-text on all meaningful images |
| 1.3.1 Info and relationships | A | Semantic tag tree reflects visual structure |
| 1.3.2 Meaningful sequence | A | Reading order enforced by layout analysis |
| 1.3.3 Sensory characteristics | A | Content not described by shape/colour alone |
| 2.4.2 Page titled | A | Title injected into PDF metadata |
| 2.4.6 Headings and labels | AA | Descriptive headings at correct hierarchy |
| 4.1.1 Parsing | A | Valid, well-formed tag structure |
| 4.1.2 Name, role, value | A | All interactive and structural elements tagged |

### Section 508 (US Federal)
Output meets Section 508 provisions for electronic documents: tagged structure, reading order, alt-text, and accessible table markup.

---

## Benefits

**For people using assistive technology**
Screen readers traverse the document in the correct logical order. Headings provide navigation landmarks. Tables are announced with column and row context. Images are described.

**For organisations**
Retroactively remediate legacy document archives at scale. No accessibility consultant required per document. Generates a per-document compliance score and audit trail.

**For AI and search systems**
Tagged, structured PDFs are directly parseable by RAG pipelines, document search indexes, and LLM ingestion tools. Eliminates chunking artefacts caused by unstructured PDF extraction.

**For developers**
The pipeline is modular — swap the LLM backend, add stages, or extend the scoring model without touching the UI or output layer.

---

## Project structure

```
app.py                  # Streamlit UI — upload, pipeline trigger, download, report
ai_pipeline.py          # Async prompt chain execution
pdf_processor.py        # PDF rendering and binary tag injection (PyMuPDF)
checker.py              # Static accessibility analysis
schemas.py              # Pydantic data models
troubleshooter.py       # LLM-assisted error diagnosis
pyproject.toml          # Python project configuration
.env.example            # Environment variable template
docs/
  README.md             # This file
  SPECIFICATION.md      # Technical specification
  REQUIREMENTS.md       # Functional and non-functional requirements
  PROOF_OF_CONCEPT.md   # POC scope, results, and limitations
```

---

## Setup

```bash
# 1. Install dependencies
pip install streamlit pymupdf httpx pillow

# 2. Set your OpenAI API key (never hardcode)
$env:LLM_API_KEY = "sk-..."          # PowerShell
export LLM_API_KEY="sk-..."          # bash/zsh

# 3. Run
python -m streamlit run app.py
```

---

## Accessibility score

The output PDF is scored 0–100 across five dimensions:

| Check | Weight |
|---|---|
| Tag Tree Complete | 25 |
| Heading Hierarchy | 20 |
| Alt-Text Coverage | 20 |
| Table Headers | 20 |
| Artifact Markers | 15 |

Score ≥ 90 = Excellent · ≥ 75 = Good · ≥ 50 = Needs improvement · < 50 = Poor
