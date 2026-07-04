# Requirements

**Project:** PDF Accessibility Studio  
**Version:** 1.0

---

## Functional requirements

### Upload
- Accept a single PDF file via drag-and-drop or file browser
- Display file name, size in KB, and page count immediately on upload
- Estimate and display total processing time before pipeline starts
- Reject non-PDF files at the UI level

### Processing
- Execute the 7-stage pipeline in strict sequential order
- Display a live progress indicator showing the current stage name and number
- Display an estimated time remaining (ETA), recalculated after each stage
- Show a per-stage progress step list (complete / active / pending states)
- Carry global context (heading depth, table continuation) across all stages
- Handle pipeline exceptions without crashing; display a readable error notification

### Output
- Compile the finalized tag tree into the PDF binary using PyMuPDF
- Write accessible metadata (title) into the output PDF
- Make the output available for download immediately after processing
- Name the output file `accessible_<original_filename>.pdf`
- Keep the download button visible across Streamlit reruns (session state)

### Accessibility report
- Compute an accessibility score (0–100) from 5 weighted checks
- Display the score with a visual bar and text label (Excellent / Good / Needs improvement / Poor)
- Colour-code the score: green ≥ 90, amber 75–89, red < 75
- Provide an expandable audit report with per-check results and detail table
- Report must remain visible alongside the download button

---

## Non-functional requirements

### Performance
- Pipeline ETA estimate based on 8 seconds per stage
- Each LLM call has a 60-second timeout
- PDF metadata compilation completes in under 2 seconds for files up to 50 MB

### Compatibility
- Python 3.11+
- Streamlit 1.35+
- PyMuPDF (fitz) 1.24+
- httpx 0.27+

### Accessibility (the app UI itself)
- UI styled to Carbon Design System v11 tokens
- Buttons meet 48px minimum touch target height
- Focus indicators use 2px solid `#0f62fe` (Carbon `$focus`)
- Colour is not the sole means of conveying information (score also shows text label)
- IBM Plex Sans font family for readability

### Security
- LLM API key read from environment variable only; never stored in source code
- No PDF content written to disk; all processing in-memory
- No user data retained between sessions

### Scalability
- Architecture supports multi-page documents; global context persists across pages
- LLM backend is swappable via `LLM_API_URL` environment variable
- Pipeline stages are modular; new stages can be inserted without UI changes

---

## Accessibility standards compliance targets

| Standard | Target |
|---|---|
| PDF/UA-1 (ISO 14289-1) | Full conformance on output documents |
| WCAG 2.2 Level AA | All applicable success criteria |
| Section 508 | Electronic document provisions |

---

## Out of scope (v1.0)

- Multi-file batch processing
- OCR language detection (assumes English)
- RTL language support
- Digital signature preservation
- Form field accessibility (AcroForms)
- Cloud storage integration
