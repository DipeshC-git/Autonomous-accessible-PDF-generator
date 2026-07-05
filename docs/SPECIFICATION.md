# Technical Specification

**Project:** PDF Accessibility Studio  
**Version:** 1.1 (Under Development)  
**Standard:** PDF/UA-1 (ISO 14289-1), WCAG 2.2, Section 508

---

## 1. Purpose

This document defines the technical architecture, component contracts, data schemas, integration interfaces, and prompt pipeline specification for the PDF Accessibility Studio.

---

## 2. System overview

A single-process Python application with two layers:

- **UI layer** — Streamlit web application handling file I/O and user interaction
- **Pipeline layer** — 9-stage sequential LLM prompt chain producing a compliant PDF tag tree

Both layers run in the same Python process. The UI drives each stage synchronously via `asyncio.run` to maintain Streamlit's single-thread model.

---

## 3. Component specifications

### 3.1 `app.py` — UI, pipeline, and orchestration

All pipeline logic, prompts, and UI code are consolidated in `app.py` for the current development build.

**Responsibilities**
- Accept PDF file upload (max 50 MB)
- Display file metadata: size, page count, estimated processing time
- Execute 9-stage pipeline with live per-stage progress indicator and ETA
- Capture Stage 3.5 output separately for metadata/bookmark injection
- Compile remediated PDF with AI-derived metadata and bookmark outline
- Display accessibility score (0–100) with colour-coded gauge
- Provide persistent download button (session state)
- Display expandable accessibility audit report with four sections

**Session state keys**

| Key | Type | Description |
|---|---|---|
| `result_pdf` | `bytes \| None` | Compiled accessible PDF binary |
| `score` | `int \| None` | Accessibility score 0–100 |
| `checks` | `dict \| None` | Per-check boolean pass/fail |
| `report` | `dict \| None` | Full audit detail including metadata and QA counts |
| `file_name` | `str \| None` | Original filename for download |
| `elapsed` | `float \| None` | Total pipeline duration in seconds |

**Configuration (environment variables)**

| Variable | Default | Description |
|---|---|---|
| `LLM_API_KEY` | `""` | OpenAI-compatible API key |
| `LLM_API_URL` | `https://api.openai.com/v1/chat/completions` | LLM endpoint (swappable) |

---

### 3.2 Pipeline stages

All stages invoked via `call_llm_stage()`. Each receives:
- System prompt with `{global_context}` injected
- Output of the previous stage as user message
- `response_format: { type: "json_object" }` enforced

**Global context object**

```json
{
  "active_table_id": null,
  "current_heading_depth": 1,
  "page_history": []
}
```

**Stage contracts**

| Stage | Input | Key output fields |
|---|---|---|
| 0 · OCR Extraction | Page image | `tokens[{ text, bounding_box[Xmin,Ymin,Xmax,Ymax] }]` |
| 1 · Layout Analysis | OCR tokens | `zones[{ id, type, reading_order, artifact }]` — type includes `Separator` |
| 2 · Unicode Repair | Layout zones | `zones: { id: clean_text }` |
| 3 · Tag Tree Assembly | Zone text | Tag tree with H1–H6, P, L, LI, Link; Artifact nodes for separators/pagination/decorative |
| 3.5 · Metadata & Bookmarks | Tag tree + zone text | `{ metadata: { title, subject, keywords, language, author }, bookmarks: [{ title, level, page_number, children[] }] }` |
| 4 · Image Alt-Text | Tag tree | Tag tree with `alt_text` and `Artifact` on image nodes |
| 5 · Table Parsing | Tag tree | Tag tree with TH/TD/scope/id/headers/colspan/rowspan/Summary/Caption |
| 6 · Contact Link Detection | Tag tree | Tag tree with `<Link href="mailto:/tel:/https:">` nodes |
| 7 · Compliance QA | Complete tag tree | Final corrected tag tree + `qa_summary` |

**Stage 3.5 is captured separately.** The orchestration loop detects `stage_name == "Step 3.5: Metadata & Bookmarks"` and stores the output in `metadata_bookmarks`, independent of the main `pipeline_input` chain. This is passed to `compile_pdf_tags()` and `compute_accessibility_score()`.

---

### 3.3 Artifact tagging rules (Stage 3)

| Zone type | Required tag | Screen reader behaviour |
|---|---|---|
| Separator (rule, line, dash, whitespace, ornament) | `<Artifact Type=Layout Subtype=Separator>` | Silently skipped |
| Running header / footer | `<Artifact Type=Pagination>` | Silently skipped |
| Decorative image / background | `<Artifact Type=Layout>` | Silently skipped |
| Section boundary after separator | Communicated by the following heading tag | No `<Sect>` on the separator itself |

**Prohibited mis-taggings for separators:** `<P>`, `<Div>`, `<Span>`, `<Sect>`. Any of these cause screen readers to announce "blank" or incorrectly announce a structural section.

---

### 3.4 Table cell integrity rules (Stage 5)

| Rule | Requirement |
|---|---|
| Header scope | Every `<TH>` has `scope` = col / row / colgroup / rowgroup |
| Header ID | Every `<TH>` has a unique `id` attribute |
| Data cell association | Every `<TD>` has `headers` attribute listing all applicable `<TH>` IDs |
| Empty cells | `<TD>` or `<TH>` with empty text must be kept; never dropped |
| Merged cells | `colspan=N` and/or `rowspan=N` declared on the tag node |
| Split headers | `<TD>` `headers` lists full chain from top-level to immediate header |
| Complex table Summary | `Summary` attribute on `<Table>` when multiple header rows or mixed axis |
| Caption | `<Caption>` as first child of `<Table>` when visible |

---

### 3.5 `compile_pdf_tags()` — PDF binary compilation

```python
compile_pdf_tags(
    original_pdf_bytes: bytes,
    finalized_tag_tree: dict,
    metadata_bookmarks: dict | None = None,
) -> bytes
```

Steps:
1. Open original PDF from bytes (PyMuPDF)
2. Build metadata dict from `metadata_bookmarks["metadata"]`, falling back to original doc values for any missing field
3. Call `doc.set_metadata(meta)` with 8 writable keys only (`title`, `author`, `subject`, `keywords`, `creator`, `producer`, `creationDate`, `modDate`)
4. Convert `metadata_bookmarks["bookmarks"]` to PyMuPDF TOC format via `_bookmarks_to_toc()`
5. Call `doc.set_toc(toc)` to write the navigation outline
6. Save with `garbage=4, deflate=True`
7. Return recompiled bytes

**`_bookmarks_to_toc(bookmarks)`** recursively converts `{ title, level, page_number, children[] }` tree to PyMuPDF's flat `[[level, title, page], ...]` format.

---

### 3.6 Compliance QA checks (Stage 7)

Performed in sequence; each correction reported in `qa_summary`:

| # | Check | Action | `qa_summary` key |
|---|---|---|---|
| 1 | Separator artifact audit | Re-tag mis-tagged separators; unwrap mis-used `<Sect>` | `separators_retagged` |
| 2 | Empty tag removal | Remove whitespace-only nodes (except `<TD>`/`<TH>`) | `empty_tags_removed` |
| 3 | Table cell integrity | Fix scope/id/headers/colspan/rowspan; insert missing cells | `cells_fixed` |
| 4 | Alt-text audit | Flag images missing alt-text | `alt_text_missing` |
| 5 | Table header audit | Flag tables missing `<TH scope>` | `table_headers_missing` |
| 6 | Heading sequence | Insert synthetic `[Section continued]` for skipped levels | `heading_gaps_corrected` |
| 7 | Link integrity | Remove `<Link>` nodes with no `href` and no `ActualText` | `links_removed` |

---

## 4. Accessibility scoring model

Seven binary checks, total 100 points:

```
tag_tree_complete    18 pts   finalized_tree["status"] == "success"
heading_hierarchy    18 pts   tree status != "error"
alt_text_coverage    18 pts   all non-decorative images processed
table_headers        18 pts   all tables mapped with TH scope
artifact_markers     12 pts   separators and decoratives as Artifact
contact_links         8 pts   contact link detection completed
metadata_bookmarks    8 pts   AI-derived title present + bookmarks generated
──────────────────────────
Total               100 pts
```

Score bands: ≥ 90 Excellent · ≥ 75 Good · ≥ 50 Needs improvement · < 50 Poor

---

## 5. UI design system

Carbon Design System v11 tokens injected via `st.markdown(unsafe_allow_html=True)`.

| Token | Value | Usage |
|---|---|---|
| `$interactive` | `#0f62fe` | Buttons, progress bar, active step dots |
| `$layer-01` | `#f4f4f4` | Tile backgrounds, metric cards |
| `$border-subtle-01` | `#e0e0e0` | Dividers, tile borders |
| `$text-primary` | `#161616` | Body text, headings |
| `$text-secondary` | `#525252` | Labels, captions, helper text |
| `$support-success` | `#24a148` | Score ≥ 90, pass check marks |
| `$support-warning` | `#f1c21b` | Score 75–89 |
| `$support-error` | `#da1e28` | Score < 75, fail marks |

**Typography:** IBM Plex Sans · **Buttons:** `border-radius: 0`, 48px min-height, 2px focus ring

---

## 6. Security

- API keys read from environment variables only — never in source code
- No PDF content written to disk; all processing in-memory
- No user data retained between sessions
- Session state cleared on new file upload
