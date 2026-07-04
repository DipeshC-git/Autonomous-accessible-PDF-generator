# Technical Specification

**Project:** PDF Accessibility Studio  
**Version:** 1.0  
**Standard:** PDF/UA-1 (ISO 14289), WCAG 2.2, Section 508

---

## 1. Purpose

This document defines the technical architecture, component contracts, data schemas, and integration interfaces for the PDF Accessibility Studio pipeline.

---

## 2. System overview

The system is a single-process Python application with two layers:

- **UI layer** — Streamlit web application handling file I/O and user interaction
- **Pipeline layer** — Asynchronous 7-stage LLM prompt chain producing a compliant PDF tag tree

Both layers run in the same Python process. The UI drives the pipeline synchronously (via `asyncio.run`) to maintain Streamlit's single-thread execution model.

---

## 3. Component specifications

### 3.1 `app.py` — UI and orchestration

**Responsibilities**
- Accept PDF file upload (max 50 MB)
- Display file metadata: size, page count, estimated processing time
- Invoke the pipeline and stream live stage-by-stage progress
- Display accessibility score (0–100) with visual indicator
- Provide download of the remediated PDF
- Display expandable accessibility audit report
- Persist results in `st.session_state` across Streamlit reruns

**State model**

| Key | Type | Description |
|---|---|---|
| `result_pdf` | `bytes \| None` | Compiled accessible PDF binary |
| `score` | `int \| None` | Accessibility score 0–100 |
| `checks` | `dict \| None` | Per-check boolean results |
| `report` | `dict \| None` | Human-readable audit detail |
| `file_name` | `str \| None` | Original filename for download |
| `elapsed` | `float \| None` | Total pipeline duration (seconds) |

**Configuration (environment variables)**

| Variable | Default | Description |
|---|---|---|
| `LLM_API_KEY` | `""` | OpenAI-compatible API key |
| `LLM_API_URL` | `https://api.openai.com/v1/chat/completions` | LLM endpoint |

---

### 3.2 Pipeline stages

Each stage is invoked via `call_llm_stage()`. All stages receive:
- The formatted system prompt (with `{global_context}` injected)
- The output of the previous stage as user message content
- `response_format: { type: "json_object" }` enforced

**Global context object**

```json
{
  "active_table_id": null,
  "current_heading_depth": 1,
  "page_history": []
}
```

Carried across all stages and updated after each page.

**Stage contracts**

| Stage | Input | Output schema |
|---|---|---|
| 0 · OCR Extraction | Page image reference | `{ tokens: [{ text, bounding_box: [Xmin,Ymin,Xmax,Ymax] }] }` |
| 1 · Layout Analysis | OCR token array | `{ zones: [{ id, type, reading_order }] }` |
| 2 · Unicode Repair | Layout zones | `{ zones: { id: clean_text } }` |
| 3 · Tag Tree Assembly | Ordered zone text | PDF tag tree JSON with H1–H6, P, L, LI, Link, Artifact |
| 4 · Image Alt-Text | Tag tree + image refs | Tag tree extended with alt_text and Artifact flags |
| 5 · Table Parsing | Tag tree with Tables | Tag tree with TH/TD/colspan/rowspan resolved |
| 6 · Compliance QA | Complete tag tree | Final corrected, compliant tag tree JSON |

---

### 3.3 `pdf_processor.py` — Binary handling

**`compile_pdf_tags(original_pdf_bytes, finalized_tag_tree) → bytes`**

Uses PyMuPDF (`fitz`) to:
1. Open the original PDF from bytes
2. Write accessible metadata (title, existing author/subject/keywords preserved)
3. Save with garbage collection level 4 and deflate compression
4. Return the recompiled binary

Metadata keys written (PyMuPDF-valid subset only):
`title`, `author`, `subject`, `keywords`, `creator`, `producer`, `creationDate`, `modDate`

---

### 3.4 `checker.py` — Static analysis

Provides a standalone accessibility check on any PDF without running the pipeline. Returns an `AccessibilityReport` with structural findings based on PyMuPDF's tag tree inspection.

---

### 3.5 `schemas.py` — Data models

Pydantic models used for structured validation across pipeline stages:

- `GlobalContext` — cross-page state tracker
- `TagTree` — validated tag tree structure
- `PipelineError` — structured error with `step`, `exception_message`, `is_fallback`

---

### 3.6 `troubleshooter.py` — Error handling

Two-phase LLM-assisted diagnosis:
1. **Diagnose** — given a `PipelineError`, generates clarifying questions
2. **Resolve** — given user answers, returns a resolution plan

---

## 4. LLM interface

**Model:** `gpt-4o` (multi-modal, required for stages 0 and 1)  
**API:** OpenAI Chat Completions (`/v1/chat/completions`)  
**Timeout:** 60 seconds per stage call  
**Response format:** `json_object` (enforced at API level)

**Payload structure**
```json
{
  "model": "gpt-4o",
  "messages": [
    { "role": "system", "content": "<stage prompt with injected context>" },
    { "role": "user",   "content": "<previous stage JSON output>" }
  ],
  "response_format": { "type": "json_object" }
}
```

---

## 5. Accessibility scoring model

Score is computed from 5 binary checks, each with a fixed point weight:

```
tag_tree_complete   25 pts   → finalized_tree["status"] == "success"
heading_hierarchy   20 pts   → no error status on tree
alt_text_coverage   20 pts   → all non-decorative images processed
table_headers       20 pts   → all tables mapped with TH scope
artifact_markers    15 pts   → headers/footers marked as Artifact
─────────────────────────
Total               100 pts
```

Score bands:
- ≥ 90 — Excellent
- ≥ 75 — Good
- ≥ 50 — Needs improvement
- < 50 — Poor

---

## 6. UI design system

The UI uses Carbon Design System v11 token values injected as CSS via `st.markdown(unsafe_allow_html=True)`.

**Key tokens used**

| Token | Value | Usage |
|---|---|---|
| `$interactive` | `#0f62fe` | Primary buttons, progress fill, active indicators |
| `$layer-01` | `#f4f4f4` | Tile backgrounds, metric cards |
| `$border-subtle-01` | `#e0e0e0` | Dividers, tile borders |
| `$text-primary` | `#161616` | Body text, headings |
| `$text-secondary` | `#525252` | Captions, labels, helper text |
| `$support-success` | `#24a148` | Score ≥ 90, pass indicators |
| `$support-warning` | `#f1c21b` | Score 75–89 |
| `$support-error` | `#da1e28` | Score < 75, fail indicators |

**Typography:** IBM Plex Sans (loaded from Google Fonts CDN)

---

## 7. Security considerations

- API keys are never hardcoded; read from environment variables only
- No user data is stored server-side; all processing is in-memory
- PDF bytes are not written to disk at any point
- Session state is cleared on new file upload
