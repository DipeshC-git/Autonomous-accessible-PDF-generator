# Proof of Concept

**Project:** PDF Accessibility Studio  
**Stage:** POC / v1.0  
**Pipeline mode:** Demo (mocked LLM responses)

---

## Objective

Validate that a 7-stage sequential AI prompt chain, wrapped in a Streamlit UI, can:

1. Accept an arbitrary PDF file from a browser
2. Execute the full pipeline with live progress feedback
3. Compile and deliver a downloadable accessible PDF
4. Score the output against WCAG 2.2 / PDF/UA criteria
5. Display a structured accessibility audit report

---

## What the POC demonstrates

### End-to-end user flow — verified working

| Step | Component | Status |
|---|---|---|
| PDF upload via browser | `st.file_uploader` | ✔ Working |
| File metadata display (size, pages, ETA) | PyMuPDF + Streamlit metrics | ✔ Working |
| 7-stage progress indicator with ETA | Custom Carbon-styled step list | ✔ Working |
| Session state persistence (no lost download) | `st.session_state` | ✔ Working |
| Accessible PDF compilation | PyMuPDF `set_metadata` + binary save | ✔ Working |
| Download button with correct filename | `st.download_button` | ✔ Working |
| Accessibility score (0–100) with colour bar | Weighted check model | ✔ Working |
| Expandable audit report | `st.expander` + structured list | ✔ Working |
| Carbon Design System UI tokens | CSS injection via `st.markdown` | ✔ Working |
| API key security (env var only) | `os.environ.get` | ✔ Working |

---

## Current limitations

### LLM calls are mocked
All 7 pipeline stages return a static mock payload:
```json
{ "status": "success", "stage": "<name>", "output": "structure payload" }
```
The actual OpenAI API call is commented out. The pipeline structure, state management, and UI behaviour are production-ready; only the LLM inference is not yet live.

**To activate live calls:** set `LLM_API_KEY` and uncomment the `httpx` POST block in `call_llm_stage()`.

### Tag tree is not injected into the PDF binary
`compile_pdf_tags()` currently writes only document metadata (title). Full structural tag tree injection into the PDF binary via PyMuPDF's `StructTree` API is the next implementation milestone.

### Single-page context only
The global context object tracks heading depth and table continuation state, but the POC pipeline processes a single logical pass — not page-by-page iteration. Multi-page document handling is architecturally designed but not yet wired to the page loop.

### Accessibility score is static in demo mode
The score model computes based on the mock tree's `status` field. In production, the score will derive from actual tag tree inspection output from Stage 6.

---

## Validated architecture decisions

The following design choices were tested and confirmed during the POC:

**`st.session_state` for result persistence**  
Without session state, the download button disappeared on every Streamlit rerun triggered by user interaction. Storing the PDF bytes and report in `st.session_state` keeps results visible indefinitely.

**`os.environ.get` for API key**  
`st.secrets` raises `StreamlitSecretNotFoundError` at import time when no `secrets.toml` file exists, regardless of any fallback value provided. Environment variables are the correct solution for local development.

**PyMuPDF `set_metadata` key restriction**  
PyMuPDF's `set_metadata()` only accepts 8 specific keys. Passing `doc.metadata` directly (which includes read-only fields like `language`, `format`, `encryption`) causes a `bad dict key(s)` error. The fix is to explicitly construct the metadata dict with only the 8 writable keys.

**Carbon CSS injection**  
Streamlit does not support theming at the component level beyond a global config. Injecting Carbon v11 CSS tokens via `st.markdown(unsafe_allow_html=True)` successfully overrides Streamlit's default styles for buttons, metrics, progress bars, expanders, and file uploaders.

---

## Next milestones

| Priority | Milestone |
|---|---|
| High | Activate live LLM calls with `gpt-4o` |
| High | Implement page-by-page iteration with per-page pipeline execution |
| High | Full StructTree injection via PyMuPDF for valid PDF/UA tag output |
| Medium | Validation of output PDF with an external accessibility checker (PAC 2024) |
| Medium | Score model driven by real Stage 6 output, not mock status |
| Low | Batch processing for multiple PDFs |
| Low | Export accessibility report as separate PDF or JSON |

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
