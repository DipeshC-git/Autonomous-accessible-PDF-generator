# Requirements

**Project:** PDF Accessibility Studio  
**Version:** 1.1 (Under Development)

---

## Functional requirements

### Upload
- Accept a single PDF file via drag-and-drop or file browser
- Display file name, size in KB, and page count immediately on upload
- Estimate and display total processing time before pipeline starts
- Reject non-PDF files at the UI level

### Processing
- Execute a 9-stage pipeline in strict sequential order
- Display a live progress indicator showing stage name and number (e.g. `Stage 4 / 9`)
- Display estimated time remaining, recalculated after each stage
- Show a Carbon-styled per-stage step list (complete / active / pending dots)
- Carry global context (heading depth, table continuation) across all stages
- Capture Stage 3.5 output separately for metadata and bookmark injection
- Handle pipeline exceptions without crashing; display a Carbon inline error notification

### Tag tree — artifact tagging
- Separator zones (horizontal rules, lines, dash sequences, whitespace, ornaments) tagged as `<Artifact Type=Layout Subtype=Separator>`
- Running headers and footers tagged as `<Artifact Type=Pagination>`
- Decorative images and backgrounds tagged as `<Artifact Type=Layout>`
- No separator may be tagged `<P>`, `<Div>`, `<Span>`, or `<Sect>`

### Tag tree — table compliance
- Every `<TH>` must have a `scope` attribute: col / row / colgroup / rowgroup
- Every `<TH>` must have a unique `id` attribute
- Every `<TD>` must have a `headers` attribute referencing all applicable `<TH>` IDs
- Empty cells must be retained as `<TD>` or `<TH>` — never dropped
- Merged cells must declare `colspan=N` and/or `rowspan=N`
- Multi-level stacked headers — data cells reference the full ID chain
- Complex tables carry a `Summary` attribute on the `<Table>` tag
- Visible captions tagged `<Caption>` as first child of `<Table>`

### Tag tree — contact links
- Email addresses wrapped in `<Link href="mailto:...">` with `ActualText`
- Phone numbers wrapped in `<Link href="tel:+...">` normalised to E.164
- Bare URLs and anchor text hyperlinks wrapped in `<Link href="...">`

### Tag tree — image alt-text
- Alt-text derived from surrounding text context (paragraph, caption, heading before/after)
- Alt-text must be specific and contextual; prohibits "image", "photo", "figure", "chart"
- Decorative images, spacers, and watermarks set `Artifact=True`, `alt_text=null`

### Output
- Inject AI-derived metadata (title, subject, keywords, language) via `set_metadata()`
- Inject hierarchical bookmark outline via `set_toc()` from Stage 3.5 headings
- Compile finalized tag tree into the PDF binary using PyMuPDF
- Make remediated PDF available for download immediately after processing
- Name output file `accessible_<original_filename>.pdf`
- Keep download button visible across Streamlit reruns (session state)

### Accessibility report
- Compute accessibility score (0–100) from 7 weighted checks
- Display score with colour-coded bar and text label (Excellent / Good / Needs improvement / Poor)
- Display four expandable sections: score breakdown, accessibility audit, document metadata, QA corrections
- QA corrections table shows: separators retagged, empty tags removed, cells fixed, heading gaps corrected, links removed
- Report persists alongside download button after processing

---

## Non-functional requirements

### Performance
- ETA estimate based on 8 seconds per stage (configurable via `SECONDS_PER_STAGE`)
- Each LLM call has a 60-second timeout
- PDF compilation completes in under 2 seconds for files up to 50 MB

### Compatibility
- Python 3.11+
- Streamlit 1.35+
- PyMuPDF (fitz) 1.24+
- httpx 0.27+

### Accessibility (the app UI itself)
- UI styled to Carbon Design System v11 tokens
- Buttons meet 48px minimum touch target height
- Focus indicators use 2px solid `#0f62fe` (Carbon `$focus`)
- Colour is not the sole conveyor of information — score shows numeric value and text label
- IBM Plex Sans font for readability

### Security
- LLM API key read from environment variable only; never in source code
- No PDF content written to disk; all processing in-memory
- No user data retained between sessions

### Scalability
- Architecture supports multi-page documents; global context persists across pages
- LLM backend is swappable via `LLM_API_URL` environment variable
- Pipeline stages are modular; new stages can be inserted without UI changes
- Stage 3.5 output is captured independently — metadata pipeline is decoupled from tag tree pipeline

---

## Accessibility standards compliance targets

| Standard | Target |
|---|---|
| PDF/UA-1 (ISO 14289-1) | Full conformance on output documents |
| WCAG 2.2 Level AA | All applicable success criteria |
| Section 508 | Electronic document provisions |

---

## Out of scope (v1.1)

- Multi-file batch processing
- OCR language detection beyond English
- RTL language support
- Digital signature preservation
- Form field accessibility (AcroForms)
- Cloud storage integration
- Live StructTree binary injection (metadata and bookmark injection operational; full tag tree injection is next milestone)
