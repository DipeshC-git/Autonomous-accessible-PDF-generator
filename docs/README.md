# PDF Accessibility Studio

An autonomous AI pipeline that converts inaccessible PDFs into fully compliant, tagged PDF/UA and WCAG 2.2 documents — readable by humans, screen readers, and AI engines.

> **Status:** Under active development · Pipeline prompts finalised · LLM calls in demo/mock mode pending live API activation

---

## What it does

Upload any PDF — scanned, image-only, poorly structured, or exported from legacy tools. The system processes it through a 9-stage AI pipeline and returns a tagged, structured, accessible PDF with no manual intervention required.

---

## Architecture

```
User (browser)
     │
     ▼
Streamlit UI (app.py)
     │  file upload / progress / download / report
     ▼
9-Stage AI Pipeline (async, sequential)
     │
     ├─ Stage 0   · OCR Extraction          — token-level bounding box mapping
     ├─ Stage 1   · Layout Analysis         — zone classification + separator detection
     ├─ Stage 2   · Unicode Repair          — text stitching, encoding correction
     ├─ Stage 3   · Tag Tree Assembly       — semantic tags + artifact tagging rules
     ├─ Stage 3.5 · Metadata & Bookmarks    — AI-derived metadata + heading outline
     ├─ Stage 4   · Image Alt-Text          — context-aware alt-text from surrounding text
     ├─ Stage 5   · Table Parsing           — full cell integrity: TH/TD/scope/id/headers/colspan/rowspan
     ├─ Stage 6   · Contact Link Detection  — mailto/tel/href link tagging
     └─ Stage 7   · Compliance QA           — separator audit, empty tag removal, cell fix, heading repair
          │
          ▼
    compile_pdf_tags() in app.py
          │  AI metadata injection + bookmark outline + binary recompilation (PyMuPDF)
          ▼
    Accessible PDF output
```

### Key design decisions

| Decision | Rationale |
|---|---|
| Sequential prompt chain | Each stage passes structured JSON forward; no stage starts without verified prior output |
| Global context tracker | Carries heading depth and table continuation state across pages |
| Step 3.5 captured separately | Metadata and bookmarks are injected into the binary independently of the final tag tree |
| PyMuPDF `set_toc()` | Bookmark outline written natively — appears in PDF viewer navigation panel |
| Separator as `Artifact Type=Layout` | Screen readers skip entirely; never tagged `<P>` or `<Sect>` |
| `st.session_state` for results | Download button and report persist across Streamlit reruns |

---

## Pipeline process

### Stage 0 — OCR Extraction
Multi-modal vision pass over the raw page image. Extracts every token with normalised bounding coordinates (0–1000 scale). Reconstructs distorted, vertical, or broken-encoding characters contextually.

### Stage 1 — Layout Analysis
Maps physical zones: Header, Footer, Paragraph, Heading (1–6), List, Table, Image, Sidebar, **Separator**, Decorative. Enforces correct column reading order. **Separator** zones (horizontal rules, decorative lines, dash sequences, whitespace blocks, ornamental glyphs) are flagged `artifact=true` so Stage 3 tags them correctly.

### Stage 2 — Unicode Repair
Stitches text fragments broken across columns or line-wrapped hyphenations. Repairs corrupted unicode so output is fully searchable and TTS-compatible.

### Stage 3 — Tag Tree Assembly
Builds a valid PDF tag tree from ordered zone content. Enforces heading level continuity. Four explicit artifact tagging rules:
- **Separators** → `<Artifact Type=Layout Subtype=Separator>` — screen readers skip silently
- **Running headers/footers** → `<Artifact Type=Pagination>`
- **Decorative images/backgrounds** → `<Artifact Type=Layout>`
- **Section boundaries** — communicated by the heading tag, never by `<Sect>` on a separator

### Stage 3.5 — Metadata & Bookmarks
Derives document metadata from content (title from H1, subject, keywords, language). Builds a hierarchical bookmark outline from all H1–H6 headings. Output is captured separately and injected into the PDF binary via `set_metadata()` and `set_toc()`.

### Stage 4 — Image Alt-Text
Reads the surrounding paragraph, caption, heading, or list item (before and after the image zone) as the primary context signal. Combines visual analysis with context to write specific, meaningful alt-text. Prohibits generic strings like "image", "photo", "figure", "chart".

### Stage 5 — Table Parsing
Seven explicit PDF/UA rules:
1. **Header cells** `<TH>` with correct `scope` (col / row / colgroup / rowgroup)
2. **Data cells** `<TD>` with `headers` attribute referencing all parent `<TH>` IDs
3. **Empty cells** retained as `<TD>` with empty text — never dropped; preserves grid structure for screen readers
4. **Merged cells** carry `colspan=N` and/or `rowspan=N`; merged headers get `scope=colgroup/rowgroup`
5. **Split headers** (multi-level stacked) — every data cell references the full ID chain from top to immediate header
6. **Complex table Summary** attribute for tables with multiple header rows or mixed row/column headers
7. **Caption** tagged `<Caption>` as first child of `<Table>` when present

### Stage 6 — Contact Link Detection
Scans the tag tree for contact detail patterns. Applies:
- Email addresses → `<Link href="mailto:...">` with `ActualText`
- Phone numbers → `<Link href="tel:+...">` normalised to E.164
- Bare URLs and anchor text hyperlinks → `<Link href="...">` with descriptive label preserved

### Stage 7 — Compliance QA
Seven sequential checks with automatic correction:
1. **Separator audit** — mis-tagged separators (`<P>`, `<Div>`, `<Sect>`) corrected to `<Artifact Type=Layout Subtype=Separator>`
2. **Empty tag removal** — whitespace-only nodes removed (except `<TD>`/`<TH>` which are always kept)
3. **Table cell integrity** — scope/id/headers/colspan/rowspan corrected; missing cells inserted
4. **Alt-text audit** — missing alt-text flagged
5. **Table header audit** — tables without `<TH scope>` flagged
6. **Heading sequence** — skipped levels corrected with synthetic `[Section continued]` heading
7. **Link integrity** — `<Link>` nodes without `href` removed

Outputs a `qa_summary` with counts of every correction made.

---

## Accessibility standards

### PDF/UA (ISO 14289-1)
Output conforms to PDF/UA-1. Requirements addressed:

- Logical reading order defined in the tag tree
- All headings tagged H1–H6 with no level skips
- Tables include `<TH>` with `scope`, `id`, and `Summary` on complex tables
- All `<TD>` carry `headers` attribute referencing parent `<TH>` IDs
- Empty cells retained; merged and split cells fully annotated
- Images carry descriptive alt-text or marked as artefacts
- Separators tagged `<Artifact Type=Layout Subtype=Separator>` — not in reading order
- Running headers/footers tagged `<Artifact Type=Pagination>`
- Document title and language set in metadata
- Bookmark outline written to PDF navigation panel

### WCAG 2.2 (W3C)

| Criterion | Level | How addressed |
|---|---|---|
| 1.1.1 Non-text content | A | Context-aware alt-text; decoratives marked Artifact |
| 1.3.1 Info and relationships | A | Semantic tag tree; table headers with scope and id |
| 1.3.2 Meaningful sequence | A | Reading order enforced by layout analysis |
| 1.3.3 Sensory characteristics | A | Content not described by shape/colour alone |
| 2.4.1 Bypass blocks | A | Artifact markers let screen readers skip decorative content |
| 2.4.2 Page titled | A | Title injected from AI-derived metadata |
| 2.4.6 Headings and labels | AA | Descriptive headings at correct hierarchy |
| 4.1.1 Parsing | A | Valid, well-formed tag structure; QA removes empty/invalid nodes |
| 4.1.2 Name, role, value | A | All structural and interactive elements tagged |

### Section 508 (US Federal)
Output meets Section 508 electronic document provisions: tagged structure, reading order, alt-text, accessible table markup, and navigable bookmarks.

---

## Benefits

**For people using assistive technology**
Screen readers traverse the document in correct logical order. Separators are silently skipped. Headings provide navigation landmarks. Tables announce column and row context from correct scope/headers markup. Empty cells maintain grid alignment. Images are described with context-specific alt-text. Contact details are navigable links.

**For organisations**
Retroactively remediate legacy document archives at scale. No accessibility consultant required per document. Generates a per-document compliance score and full audit trail including QA correction counts.

**For AI and search systems**
Tagged, structured PDFs are directly parseable by RAG pipelines, document search indexes, and LLM ingestion tools. AI-derived keywords and subject metadata improve discoverability.

**For developers**
The pipeline is modular — swap the LLM backend, add stages, or extend the scoring model without touching the UI or output layer.

---

## Project structure

```
app.py                  # Streamlit UI + 9-stage pipeline prompts + orchestration
docs/
  README.md             # This file
  SPECIFICATION.md      # Technical specification
  REQUIREMENTS.md       # Functional and non-functional requirements
  PROOF_OF_CONCEPT.md   # POC scope, validated decisions, limitations, next milestones
```

---

## Setup

```bash
# 1. Install dependencies
python -m pip install streamlit pymupdf httpx pillow

# 2. Set your OpenAI API key (never hardcode)
$env:LLM_API_KEY = "sk-..."          # PowerShell
export LLM_API_KEY="sk-..."          # bash/zsh

# 3. Run
python -m streamlit run app.py
```

> **Note:** The pipeline currently runs in demo mode (mocked LLM responses).  
> To activate live processing, set `LLM_API_KEY` and uncomment the `httpx` POST block in `call_llm_stage()`.

---

## Accessibility score

Output is scored 0–100 across seven dimensions:

| Check | Weight |
|---|---|
| Tag Tree Complete | 18 |
| Heading Hierarchy | 18 |
| Alt-Text Coverage | 18 |
| Table Headers | 18 |
| Artifact Markers | 12 |
| Contact Links Tagged | 8 |
| Metadata & Bookmarks | 8 |

Score ≥ 90 = Excellent · ≥ 75 = Good · ≥ 50 = Needs improvement · < 50 = Poor
