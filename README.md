# Autonomous-accessible-PDF-generator
Use this system to convert inaccessible PDF to machine-readable PDF with full accessibility score
---
## System Architecture

To ensure speed and predictability for a user-facing tool, the architecture splits the workload: *Heavy Computer Vision and File Conversion* are handled deterministically by Python, while *Semantic Context & Tag Generation* are handled by the chained AI prompts.

### The 4-Stage Execution Loop

1. *Ingestion & Rendering:* The Python backend receives the individual PDF upload and uses pdf2image to render each page as a high-resolution PNG image.
2. *Contextual History Tracker:* A state dictionary tracking table-continuations and global heading levels is carried over across pages to prevent edge-case structural breaks.
3. *The Multi-Modal Prompt Chain:* The image and page context flow sequentially through 7 optimized AI steps.
4. *Binary Compilation:* Python takes the final validated JSON tag structure and injects it back natively into the original document binary using PyMuPDF.

---
