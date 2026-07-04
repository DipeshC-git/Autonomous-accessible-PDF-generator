# Proof of Concept

**Project:** PDF Accessibility Studio  
**Version:** 1.1 — Under Development  
**Pipeline mode:** Demo (mocked LLM responses — live API activation pending)

---

## Objective

Validate that a 9-stage sequential AI prompt chain, wrapped in a Streamlit UI, can:

1. Accept an arbitrary PDF file from a browser
2. Execute the full pipeline with live stage-by-stage progress feedback
3. Compile and deliver a downloadable accessible PDF with injected metadata and bookmarks
4. Score the output against WCAG 2.2 / PDF/UA criteria across 7 dimensions
5. Display a structured accessibility audit report with QA correction counts

---

## End-to-end user flow — verified working

| Step | Component | Status |
|---|---|---|
| PDF upload via browser | `st.file_uploader` | ✔ Working |
| File metadata display (size, pages, ETA) | PyMuPDF + Streamlit metrics | ✔ Working |
| 9-stage progress indicator with ETA | Carbon-styled step list | ✔ Working |
| Session state persistence (no lost download) | `st.session_state` | ✔ Working |
| Accessible PDF compilation | PyMuPDF `set_metadata` + `set_toc` + binary save | ✔ Working |
| AI-derived metadata injection | Stage 3.5 output → `set_metadata()` | ✔ Working (demo values) |
| Bookmark outline injection | Stage 3.5 output → `set_toc()` | ✔ Working (demo values) |
| Download button with correct filename | `st.download_button` | ✔ Working |
| Accessibility score (0–100) with colour bar | 7-check weighted model | ✔ Working |
| Expandable audit report (4 sections) | `st.expander` + Carbon structured lists | ✔ Working |
| Carbon Design System UI | CSS injection via `st.markdown` | ✔ Working |
| API key security (env var only) | `os.environ.get` | ✔ Working |
| Pipeline error notification | Carbon inline notification | ✔ Working |

---

## Pipeline prompt coverage — verified complete

| Stage | Capability added | Status |
|---|---|---|
| Stage 1 · Layout Analysis | `Separator` zone type with `artifact=true` flag | ✔ Prompt finalised |
| Stage 3 · Tag Tree Assembly | 4-rule artifact tagging (Separator/Pagination/Decorative/Sect boundary) | ✔ Prompt finalised |
| Stage 3.5 · Metadata & Bookmarks | AI-derived title/subject/keywords/language + H1–H6 bookmark outline | ✔ Prompt finalised |
| Stage 4 · Image Alt-Text | Context-from-surrounding-text; prohibits generic strings | ✔ Prompt finalised |
| Stage 5 · Table Parsing | 7-rule cell integrity: empty cells, merged cells, split headers, scope/id/headers, Summary, Caption | ✔ Prompt finalised |
| Stage 6 · Contact Link Detection | mailto/tel/href tagging with E.164 normalisation | ✔ Prompt finalised |
| Stage 7 · Compliance QA | Separator audit, empty tag removal, cell fix, heading gap, link integrity | ✔ Prompt finalised |

---

## Validated architecture decisions

**`st.session_state` for result persistence**  
Without session state, the download button disappeared on every Streamlit rerun triggered by user interaction. Storing the PDF bytes, score, checks, and report in `st.session_state` keeps all results visible indefinitely until a new file is uploaded.

**`os.environ.get` for API key**  
`st.secrets` raises `StreamlitSecretNotFoundError` at import time when no `secrets.toml` file exists, regardless of any fallback value. Environment variables are the correct solution for local development.

**PyMuPDF `set_metadata()` key restriction**  
`set_metadata()` accepts only 8 specific writable keys. Passing `doc.metadata` directly (which includes read-only fields like `language`, `format`, `encryption`) causes a `bad dict key(s)` error. The fix is to explicitly construct the metadata dict from only the 8 valid keys, with AI-derived values taking precedence over originals.

**Stage 3.5 captured outside the main pipeline chain**  
Metadata and bookmark data must be passed to `compile_pdf_tags()` and `compute_accessibility_score()` independently of the final tag tree. The orchestration loop detects the stage name and stores the parsed output in a separate `metadata_bookmarks` variable.

**`_bookmarks_to_toc()` recursive conversion**  
PyMuPDF's `set_toc()` requires a flat list `[[level, title, page], ...]`. The Stage 3.5 output is a nested tree `{ title, level, page_number, children[] }`. The helper function recursively flattens this without losing hierarchy information.

**Separator tagging — screen reader skip pattern**  
Screen readers navigate by traversing the tag tree linearly and only skip elements tagged `<Artifact>`. Separators tagged as `<P>` or `<Div>` with empty text cause NVDA/JAWS to announce "blank". Separators tagged as `<Sect>` cause false "section" announcements. The correct tag is `<Artifact Type=Layout Subtype=Separator>` — explicitly excluded from the logical reading order.

**Empty table cells must be kept**  
The empty tag removal rule in Stage 7 has an explicit exception for `<TD>` and `<TH>`. Removing empty cells breaks the grid structure that screen readers use to announce column and row position. A missing cell causes the reader to mis-announce subsequent cells in the wrong column.

**Carbon CSS injection**  
Streamlit does not support component-level theming beyond a global config. Injecting Carbon v11 CSS via `st.markdown(unsafe_allow_html=True)` successfully overrides buttons, metrics, progress bars, expanders, file uploaders, and tables.

---

## Current limitations

### LLM calls are mocked
All 9 pipeline stages return a static mock payload:
```json
{ "status": "success", "stage": "<name>", "output": "structure payload" }
```
The actual OpenAI API call is present but commented out. Pipeline structure, state management, and UI are production-ready. Only LLM inference is not yet live.

**To activate:** set `LLM_API_KEY` and uncomment the `httpx` POST block in `call_llm_stage()`.

### Tag tree not injected into PDF binary structure
`compile_pdf_tags()` injects document metadata and bookmark outline. Full structural tag tree injection into the PDF binary via PyMuPDF's `StructTree` API is the next implementation milestone.

### Single-pass context only
The global context object tracks state, but the pipeline currently processes a single logical pass rather than iterating page-by-page. Multi-page iteration is architecturally designed.

### Score uses mock status
`compute_accessibility_score()` currently derives checks from the mock tree's `status` field. In production it will inspect the actual tag tree structure from Stage 7 output.

---

## Next milestones

| Priority | Milestone |
|---|---|
| High | Activate live LLM calls with `gpt-4o` |
| High | Page-by-page pipeline iteration with per-page global context update |
| High | Full StructTree binary injection via PyMuPDF |
| Medium | Score model driven by real Stage 7 `qa_summary` output |
| Medium | External accessibility validation with PAC 2024 or axe-pdf |
| Low | Batch multi-file processing |
| Low | Export accessibility report as PDF or JSON |

---

## Environment

```
Python        3.14.6
Streamlit     1.58.0
PyMuPDF       1.28.0
httpx         0.28.1
Pillow        12.3.0
OS            Windows 10 (x64)
```
