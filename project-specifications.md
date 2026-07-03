
# Project Specification: Autonomous PDF Remediation Engine

## Core Modules to Generate

### 1. pdf_processor.py
- Function to convert incoming PDF bytes to a list of high-res PIL images.
- Low-level functions utilizing PyMuPDF to modify the PDF Structural Parent Tree and insert structural tag elements matching a target JSON schema.

### 2. ai_pipeline.py
- Asynchronous orchestration engine managing the 7-step prompt chain.
- Implements a state-machine that passes global_context (e.g., active_table_id, current_heading_depth) alongside individual page images.

### 3. schemas.py
- Pydantic models enforcing strict data structures for OCR tokens, Layout Blueprints, and Tag Trees.

### 4. app.py
- Clean FastAPI or Streamlit user interface featuring a single-file upload dropzone, a multi-step visual progress bar reflecting the active prompt step, and a download action for the remediated PDF.

