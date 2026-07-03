# Autonomous-accessible-PDF-generator
Use this system to convert inaccessible PDF to machine-readable PDF with full accessibility score.
---
# AI-powered autonomous PDF remediation engine

A zero-intervention, asynchronous Python-based system designed to ingest inaccessible PDFs, execute a sequential multi-modal AI prompting pipeline to extract structure, and rebuild fully accessible, machine-readable, PDF/UA and WCAG compliant files.

## Project structure

├── .bobrules             # IBM Bob IDE development instructions

├── project_specification # Implementation blueprint for Bob Agent

├── app.py                # User-facing web UI wrapper

├── ai_pipeline.py        # Asynchronous sequential prompt machine

├── pdf_processor.py      # Binary handling (PyMuPDF / pdf2image)

└── schemas.py            # Pydantic validation structures

## System architecture

To ensure speed and predictability for a user-facing tool, the architecture splits the workload: *Heavy Computer Vision and File Conversion* are handled deterministically by Python, while *Semantic Context & Tag Generation* are handled by the chained AI prompts.

### The 4-Stage execution loop

1. *Ingestion & Rendering:* The Python backend receives the individual PDF upload and uses pdf2image to render each page as a high-resolution PNG image.
2. *Contextual History Tracker:* A state dictionary tracking table-continuations and global heading levels is carried over across pages to prevent edge-case structural breaks.
3. *The Multi-Modal Prompt Chain:* The image and page context flow sequentially through 7 optimized AI steps.
4. *Binary Compilation:* Python takes the final validated JSON tag structure and injects it back natively into the original document binary using PyMuPDF.

## Workflow Execution Summary

When a user uploads an individual PDF document, the platform initializes pdf_processor.py to decouple structural layers. Page content is streamed into ai_pipeline.py where the 7-prompt sequence analyzes layout matrices, executes OCR corrective operations, patches header cascades, and formats tables. The resulting tag structure map is compiled and written back into the PDF binary wrapper, generating a compliant download link instantly.

---
