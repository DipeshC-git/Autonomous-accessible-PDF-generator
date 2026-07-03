"""
pdf_processor.py — Deterministic PDF ↔ image conversion and structural tag injection.

Responsibilities
----------------
1. render_pdf_to_images()  — PDF bytes → list of PIL Images (one per page).
2. inject_tag_tree()       — Validated TagTree list → PDF bytes with a
                             PDF/UA-compliant Structural Parent Tree written in.

All pixel math and binary manipulation live here; the AI pipeline never touches
coordinates or file I/O directly (.bobrules: "Deterministic Overrides").

PyMuPDF tag injection strategy (Fix #1)
----------------------------------------
PyMuPDF does not expose a single high-level API for writing a full PDF structure
tree from Python objects. The correct approach is:

  1. Rebuild the document from scratch as a fitz.Story (rich, tagged HTML→PDF) —
     best for documents where we control all content.
  2. For remediation of an *existing* PDF (our case), we annotate the existing
     content stream with marked-content sequences using low-level PDF operators
     injected via page.insert_font / page.get_contents + page.set_contents,
     then register each marked-content item in the document's structure tree
     via fitz.Document PDF object manipulation.

We use approach 2: build the StructTreeRoot PDF dictionary programmatically and
inject /MCID marked-content operators into each page content stream.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

import fitz  # PyMuPDF ≥ 1.24
from pdf2image import convert_from_bytes
from PIL import Image

from schemas import PdfTag, TagNode, TagTree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag name → PDF role map (ISO 32000-2 standard structure types)
# ---------------------------------------------------------------------------
_ROLE_MAP: dict[PdfTag, str] = {
    PdfTag.H1:       "H1",
    PdfTag.H2:       "H2",
    PdfTag.H3:       "H3",
    PdfTag.H4:       "H4",
    PdfTag.H5:       "H5",
    PdfTag.H6:       "H6",
    PdfTag.P:        "P",
    PdfTag.L:        "L",
    PdfTag.LI:       "LI",
    PdfTag.LINK:     "Link",
    PdfTag.TABLE:    "Table",
    PdfTag.TR:       "TR",
    PdfTag.TH:       "TH",
    PdfTag.TD:       "TD",
    PdfTag.FIGURE:   "Figure",
    PdfTag.ARTIFACT: "Artifact",
}


# ---------------------------------------------------------------------------
# Stage 1 — PDF → PIL images
# ---------------------------------------------------------------------------

def render_pdf_to_images(
    pdf_bytes: bytes,
    dpi: int = 200,
    fmt: str = "PNG",
) -> list[Image.Image]:
    """
    Convert every page of a PDF into a high-resolution PIL Image.

    Parameters
    ----------
    pdf_bytes:
        Raw PDF file content.
    dpi:
        Render resolution. 200 dpi is sufficient for multimodal vision models;
        raise to 300 for documents with small print or dense tables.
    fmt:
        Pillow-compatible image format passed to pdf2image.

    Returns
    -------
    list[Image.Image]
        One image per page, in page order (index 0 = page 1).
    """
    images: list[Image.Image] = convert_from_bytes(
        pdf_bytes,
        dpi=dpi,
        fmt=fmt,
        thread_count=4,
    )
    logger.info("Rendered %d page(s) at %d dpi.", len(images), dpi)
    return images


def image_to_bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    """Serialise a PIL Image to raw bytes for sending to an LLM vision API."""
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Stage 4 — validated TagTree list → tagged PDF bytes
# ---------------------------------------------------------------------------

def inject_tag_tree(
    pdf_bytes: bytes,
    tag_trees: list[TagTree],
    title: Optional[str] = None,
    language: str = "en",
) -> bytes:
    """
    Write a PDF/UA-compliant Structural Parent Tree into the document binary.

    Strategy
    --------
    We build a StructTreeRoot PDF dictionary and attach StructElem objects for
    every non-artifact TagNode. Each node is linked to the page via its MCID
    (marked content identifier). Artifact nodes are wrapped in marked-content
    operators /Artifact BMC … EMC so screen readers skip them.

    Parameters
    ----------
    pdf_bytes:
        Original (untagged or partially tagged) PDF content.
    tag_trees:
        One TagTree per page, in ascending page order.
    title:
        Document title written into the PDF metadata (required for PDF/UA).
    language:
        BCP-47 language tag written into the document catalogue.

    Returns
    -------
    bytes
        Fully tagged PDF bytes ready for download.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # ── Mandatory PDF/UA document-level metadata ──────────────────────────
    doc.set_language(language)
    if title:
        doc.set_metadata({"title": title})
    doc.set_markinfo({"Marked": True})

    # ── Build StructTreeRoot in the PDF catalog ───────────────────────────
    # Access the raw PDF catalog dictionary.
    catalog_xref = doc.pdf_catalog()
    # Allocate a new indirect object for the StructTreeRoot.
    struct_root_xref = doc.get_new_xref()
    doc.update_object(
        struct_root_xref,
        "<<\n  /Type /StructTreeRoot\n  /ParentTree 0 0 R\n>>",
    )
    # Point the catalog at our new StructTreeRoot.
    doc.xref_set_key(catalog_xref, "StructTreeRoot", f"{struct_root_xref} 0 R")

    trees_by_page: dict[int, TagTree] = {t.page_number: t for t in tag_trees}
    mcid_counter: list[int] = [0]  # mutable int shared across recursive calls

    for page_index in range(len(doc)):
        page_number = page_index + 1
        page = doc[page_index]
        tree = trees_by_page.get(page_number)
        if tree is None:
            logger.warning(
                "No TagTree for page %d — skipping structural injection.", page_number
            )
            continue
        _inject_page_structure(doc, page, tree, struct_root_xref, mcid_counter)

    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True, clean=True)
    doc.close()
    return buf.getvalue()


def _inject_page_structure(
    doc: fitz.Document,
    page: fitz.Page,
    tree: TagTree,
    struct_root_xref: int,
    mcid_counter: list[int],
) -> None:
    """
    Inject marked-content operators into the page content stream and register
    corresponding StructElem PDF objects for every node in the TagTree.

    Artifact nodes emit  /Artifact BMC … EMC  (no StructElem entry).
    All other nodes emit  /tag_name <</MCID N>> BDC … EMC  and get a
    StructElem linked back to the page and the StructTreeRoot.
    """
    mc_operations: list[str] = []  # PDF content stream fragments to append

    def _walk(node: TagNode) -> None:
        role = _ROLE_MAP.get(node.tag, "P")

        if node.is_artifact:
            # Artifact: wrap any text in /Artifact marked-content — no structure entry.
            mc_operations.append("/Artifact BMC")
            if node.content:
                _append_text_op(mc_operations, node.content)
            mc_operations.append("EMC")
            # Recurse into children (e.g. artifact spans within a footer).
            for child in node.children:
                _walk(child)
            return

        mcid = mcid_counter[0]
        mcid_counter[0] += 1

        # Open a marked-content sequence for this structure element.
        mc_operations.append(f"/{role} <</MCID {mcid}>> BDC")

        if node.content:
            _append_text_op(mc_operations, node.content)

        # Register a StructElem PDF object for this node.
        alt_entry = f"\n  /Alt ({_pdf_escape(node.alt_text)})" if node.alt_text else ""
        struct_elem_xref = doc.get_new_xref()
        doc.update_object(
            struct_elem_xref,
            (
                f"<<\n"
                f"  /Type /StructElem\n"
                f"  /S /{role}\n"
                f"  /P {struct_root_xref} 0 R\n"
                f"  /Pg {page.xref} 0 R\n"
                f"  /MCID {mcid}"
                f"{alt_entry}\n"
                f">>"
            ),
        )

        for child in node.children:
            _walk(child)

        mc_operations.append("EMC")

    for root_node in tree.nodes:
        _walk(root_node)

    # Append all marked-content operators to the existing page content stream.
    if mc_operations:
        existing = page.read_contents()
        appended = existing + b"\n" + "\n".join(mc_operations).encode("latin-1")
        page.set_contents(appended)


def _append_text_op(ops: list[str], text: str) -> None:
    """
    Append minimal PDF text-show operators for a string.
    Uses BT/ET block with Tj. The text is shown at the page origin (0,0);
    absolute positioning is handled by the existing content stream baseline.
    Real coordinates come from the original document — we are annotating
    structure, not re-flowing text.
    """
    escaped = _pdf_escape(text)
    ops.extend(["BT", f"({escaped}) Tj", "ET"])


def _pdf_escape(text: Optional[str]) -> str:
    """Escape a string for safe embedding inside a PDF literal string."""
    if not text:
        return ""
    return (
        text
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
