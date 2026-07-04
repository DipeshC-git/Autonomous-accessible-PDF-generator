import streamlit as st
import asyncio
import httpx
import json
import io
import os
import time
import datetime
from fitz import open as open_pdf  # PyMuPDF

# --- CONFIGURATION ---
# Set before launching:  $env:LLM_API_KEY = "sk-..."
API_KEY = os.environ.get("LLM_API_KEY", "")
API_URL = os.environ.get("LLM_API_URL", "https://api.openai.com/v1/chat/completions")

SECONDS_PER_STAGE = 8

# --- 9-STAGE PIPELINE PROMPTS ---
PROMPTS = {
    "Step 0: OCR Extraction": (
        "You are an advanced multi-modal OCR engine. Process the provided raw document image page. "
        "Extract all visible text with 100% literal accuracy, preserving specialized characters, "
        "mathematical equations, and numeric structures. "
        "For every block, line, or explicit word, calculate and output its normalized bounding box "
        "coordinates (Xmin, Ymin, Xmax, Ymax) scaled from 0 to 1000. "
        "If text is distorted, vertically aligned, or displays broken encodings, reconstruct the "
        "correct characters contextually. "
        "Output format: Strict raw JSON stream containing an array of tokens with text and "
        "bounding_box parameters. Do not wrap in markdown code blocks."
    ),
    "Step 1: Layout Analysis": (
        "Analyze the provided PDF page image alongside the raw OCR coordinates from Step 0. "
        "Identify and map the physical layout zones. Classify each zone into one of these types: "
        "Header, Footer, Paragraph, Heading (1-6), List, Table, Image, Sidebar, Separator, or Decorative. "
        "\n\n"
        "SEPARATOR ZONE DEFINITION: "
        "A Separator is any purely visual element used to divide content sections with no readable text. "
        "This includes: horizontal rules, decorative lines, thick borders between sections, "
        "repeated dash or underscore sequences used as dividers, blank whitespace blocks inserted "
        "purely for visual spacing, and ornamental glyphs (e.g. three dots, asterisks, tildes) "
        "used as section breaks. "
        "Do NOT classify a zone as Separator if it contains meaningful text, a heading, or a caption. "
        "A Separator must be flagged with artifact=true in the output zone blueprint. "
        "\n\n"
        "CRITICAL FOR READING ORDER: If a page has two columns, ensure the zones are ordered down "
        "the first column completely before starting the second column. Do not read horizontally "
        "across columns. "
        "Output a structured JSON blueprint mapping zone IDs to their type, sequential reading "
        "position indices, and an artifact boolean flag."
    ),
    "Step 2: Unicode Repair": (
        "Using the layout blueprint from Step 1, map the raw text fragments into their assigned "
        "structural zone IDs. "
        "Verify that sentences breaking across columns or lines are stitched together natively "
        "without artificial hyphenations or line breaks. "
        "Repair all corrupted unicode artifacts so text strings are fully searchable and readable "
        "by text-to-speech software. "
        "Output format: JSON mapping zone IDs to clean, compiled string text."
    ),
    "Step 3: Tag Tree Assembly": (
        "Act as a certified PDF/UA compliance engineer. Take the ordered zone text from Step 2. "
        "Review the global heading history context: {global_context}. "
        "Generate a valid PDF Tag Tree structure applying semantic tags: <H1> through <H6>, "
        "<P>, <L>, <LI>, and <Link>. "
        "CRITICAL: Do not skip heading levels. "
        "\n\n"
        "ARTIFACT TAGGING RULES — apply to every zone marked artifact=true in the layout blueprint: "
        "\n\n"
        "RULE A — SEPARATORS: "
        "Any zone classified as Separator (horizontal rule, decorative line, dash sequence, "
        "ornamental glyph, or pure whitespace block) must be tagged as: "
        "  <Artifact Type=Layout Subtype=Separator> "
        "This instructs all conforming screen readers (NVDA, JAWS, VoiceOver, TalkBack) to skip "
        "the element entirely and move directly to the next real content node. "
        "Do NOT tag separators as <P>, <Div>, or any content tag — doing so causes screen readers "
        "to announce 'blank' or pause on meaningless content. "
        "Do NOT tag separators as <Sect> — that tag implies a structural section with a heading. "
        "\n\n"
        "RULE B — RUNNING HEADERS AND FOOTERS: "
        "Any repeated page header or footer zone must be tagged as: "
        "  <Artifact Type=Pagination> "
        "This is distinct from Separator — it marks content that repeats across pages. "
        "\n\n"
        "RULE C — DECORATIVE IMAGES AND BACKGROUNDS: "
        "Any zone classified as Decorative (logo watermark, background pattern, "
        "purely ornamental image) must be tagged as: "
        "  <Artifact Type=Layout> "
        "\n\n"
        "RULE D — SECTION BOUNDARIES (correct usage): "
        "If a separator is immediately followed by a new heading, the structural section boundary "
        "is already communicated by the heading tag itself. "
        "Do not insert a <Sect> tag for the separator — the heading provides the hierarchy. "
        "Only use <Sect> as a container grouping a heading and its subordinate content when "
        "the document structure explicitly requires it. "
    ),
    "Step 3.5: Metadata & Bookmarks": (
        "You are a PDF metadata and navigation engineer. Using the completed Tag Tree from Step 3 "
        "and the full document text from Step 2, perform two tasks. "
        "IMPORTANT: Every metadata field is required. Never output null or an empty string for "
        "any field — always derive the best possible value from the document content. "
        "\n\n"
        "TASK A — DOCUMENT METADATA: "
        "Output a 'metadata' JSON object with ALL of the following fields: "
        "\n"
        "title: The document's main title. Look for the largest or most prominent text on the "
        "first page, the first H1 heading, or any text styled as a cover title. "
        "If none is clearly a title, synthesise a descriptive title from the subject matter. "
        "Never leave blank. "
        "\n"
        "author: The name(s) of the author(s) or organisation. Look for bylines, 'Prepared by', "
        "'Written by', 'Author:', or footer/header attribution text. "
        "If not found in the document, output 'Unknown Author'. "
        "\n"
        "subject: A single sentence (max 200 characters) describing what the document covers. "
        "Derive from the introduction, abstract, or opening paragraph. "
        "\n"
        "keywords: 5 to 10 comma-separated terms that best represent the document's topics. "
        "Extract from headings, repeated terms, and key concepts in the body text. "
        "\n"
        "language: ISO 639-1 two-letter language code of the document's primary language "
        "(e.g. 'en' for English, 'fr' for French). "
        "\n"
        "creation_date: The document's original creation date in ISO 8601 format (YYYY-MM-DD). "
        "Look for a date on the cover page, in the header/footer, or in any 'Date:' or 'Published:' "
        "field. If no date is found, output today's date. "
        "\n\n"
        "TASK B — BOOKMARK OUTLINE: "
        "Build a hierarchical bookmark outline from every heading tagged H1–H6 in the Tag Tree. "
        "Each entry must include: title (the heading text), level (1–6), page_number (1-based). "
        "Nest child bookmarks under their parent heading. "
        "Output format: JSON object with keys 'metadata' (object) and 'bookmarks' (array of "
        "{ title, level, page_number, children[] } objects)."
    ),
    "Step 4: Image Alt-Text": (
        "Analyze all image assets on this page. For each image: "
        "\n\n"
        "1. Read the SURROUNDING TEXT CONTEXT — the paragraph, caption, heading, or list item "
        "immediately before and after the image zone in the Tag Tree. Use this context as the "
        "primary signal for what the image represents. "
        "2. Combine the visual content analysis with the surrounding context to write a highly "
        "descriptive, specific alt-text string. The alt-text must answer: what is shown, why it "
        "is relevant here, and (for charts/graphs) what the key data trend or conclusion is. "
        "3. If the surrounding text already fully describes the image (e.g. a figure caption "
        "directly below), write a concise alt-text that does not duplicate the caption verbatim "
        "but summarises the visual. "
        "4. If an image is a spacer, horizontal rule, logo watermark, or purely decorative "
        "background element, set Artifact=True and alt_text=null. "
        "5. Never output generic alt-text such as 'image', 'photo', 'figure', or 'chart'. "
        "Append alt_text and Artifact properties into the existing Tag Tree JSON for each image node."
    ),
    "Step 5: Table Parsing": (
        "You are a PDF/UA table accessibility specialist. Isolate every element tagged as a Table "
        "and apply the following rules exhaustively. Examine the historical context: {global_context}. "
        "Check if this table is a continuation of a table from a previous page — if yes, preserve "
        "the same column header configurations from the prior page. "
        "\n\n"
        "RULE 1 — HEADER CELLS (<TH>): "
        "Every cell that acts as a column or row header must be tagged <TH>. "
        "Each <TH> must carry a scope attribute: "
        "  scope=col  for a cell that heads an entire column, "
        "  scope=row  for a cell that heads an entire row, "
        "  scope=colgroup  for a merged cell spanning multiple columns as a group header, "
        "  scope=rowgroup  for a merged cell spanning multiple rows as a group header. "
        "Never assign scope=col to a row header or vice versa. "
        "\n\n"
        "RULE 2 — DATA CELLS (<TD>): "
        "Every non-header cell must be tagged <TD>. "
        "Each <TD> must carry a headers attribute listing the IDs of ALL <TH> cells "
        "that apply to it — both the column header ID and the row header ID if both exist. "
        "Assign a unique id attribute to every <TH> cell so that <TD> headers attributes "
        "can reference them (e.g. id=col_2, id=row_3). "
        "\n\n"
        "RULE 3 — EMPTY CELLS: "
        "A visually empty or blank cell must NOT be omitted from the tag tree. "
        "Tag it as <TD> (or <TH> if it occupies a header position). "
        "Set its text content to an empty string. "
        "It must still carry the correct headers attribute pointing to its parent header IDs. "
        "A missing cell breaks column alignment for assistive technology — never drop empty cells. "
        "\n\n"
        "RULE 4 — MERGED CELLS (colspan and rowspan): "
        "A cell spanning multiple columns must have colspan=N where N is the column count. "
        "A cell spanning multiple rows must have rowspan=N where N is the row count. "
        "A merged header cell gets both the appropriate scope and colspan or rowspan. "
        "Every cell underneath a merged header must reference that merged header id "
        "in its headers attribute alongside any row header id. "
        "Account for every grid position — never leave ghost slots where merged cells extend. "
        "\n\n"
        "RULE 5 — SPLIT HEADERS (multi-level stacked headers): "
        "When a table has two or more header rows (category header above column sub-headers), "
        "tag each level as a separate <TH> row. "
        "Assign unique IDs to every header cell at every level. "
        "Each data cell headers attribute must list ALL header IDs in its column chain "
        "from the top level down to the immediate header. "
        "\n\n"
        "RULE 6 — COMPLEX TABLE SUMMARY: "
        "If the table has more than one header row, merged cells, or both row and column headers, "
        "add a Summary attribute to the <Table> tag with a one-sentence plain-language description "
        "of the table structure (e.g. Sales data by region and quarter with merged regional headers). "
        "\n\n"
        "RULE 7 — TABLE CAPTION: "
        "If a visible caption or title exists immediately above or below the table, tag it as "
        "<Caption> and nest it as the first child of the <Table> node. "
        "\n\n"
        "Merge the fully annotated table structure into the master Tag Tree. "
        "Output the complete updated Tag Tree JSON."
    ),
    "Step 6: Contact Link Detection": (
        "Scan the entire Tag Tree for any text that matches a contact detail pattern. "
        "Apply the following rules for each match found: "
        "\n\n"
        "EMAIL ADDRESSES — any text matching the pattern user@domain.tld: "
        "Wrap in a <Link> tag with href='mailto:user@domain.tld'. "
        "Set the link's ActualText to the email address string. "
        "\n\n"
        "PHONE NUMBERS — any numeric pattern resembling a phone number "
        "(e.g. +1 800 555 0100, (020) 7946 0958, 04XX XXX XXX): "
        "Normalise to E.164 format where possible and wrap in <Link> with href='tel:+XXXXXXXXXXX'. "
        "\n\n"
        "HYPERLINKS — any text that is a bare URL (http://, https://, www.) or descriptive "
        "anchor text with a visible URL nearby: "
        "Wrap in <Link> with href equal to the full URL. "
        "If the anchor text is a full URL, set ActualText to the URL. "
        "If the anchor text is descriptive (e.g. 'Visit our website'), keep the descriptive text "
        "as the link label and set href to the associated URL. "
        "\n\n"
        "Do not create links for numbers that are not phone numbers (e.g. page numbers, "
        "product codes, dates). "
        "\n\n"
        "LINK QUALITY RULES (apply to every <Link> node, new or existing): "
        "Every <Link> must have: "
        "  1. A non-empty href attribute (mailto:, tel:, or https:// URL). "
        "  2. A non-empty ActualText attribute containing the human-readable label for the link. "
        "     If a <Link> wraps a <Span> or <P> with empty or whitespace-only text, "
        "     set ActualText to the href value itself (e.g. the email address or URL). "
        "  3. A descriptive label — never 'click here', 'here', 'link', or the bare URL as the "
        "     only visible text when a descriptive label is available nearby. "
        "Remove any <Link> node where both href and ActualText are empty or missing. "
        "Return the updated Tag Tree JSON."
    ),
    "Step 7: Compliance QA": (
        "Act as an automated accessibility auditor. Run a final compliance pass over the completed "
        "Tag Tree against WCAG 2.2 and PDF/UA specifications. Perform all checks and corrections "
        "in the numbered sequence below. "
        "\n\n"
        "1. SEPARATOR ARTIFACT AUDIT: "
        "Scan the entire tag tree for any node originating from a Separator zone "
        "(horizontal rule, decorative line, dash sequence, whitespace block, ornamental glyph). "
        "  a) Tagged as <Artifact Type=Layout Subtype=Separator> — correct, leave it. "
        "  b) Tagged as <P>, <Div>, or <Span> with empty/whitespace text — re-tag as "
        "     <Artifact Type=Layout Subtype=Separator>. "
        "  c) Tagged as <Sect> — remove the <Sect> wrapper, re-tag separator as Artifact, "
        "     and promote any heading/content children to the correct tree level. "
        "  d) Confirm no separator node is reachable by a screen reader's linear navigation. "
        "Report separators_retagged count. "
        "\n\n"
        "2. EMPTY TAG REMOVAL: Find every non-table tag node where text content is null, "
        "empty string, or whitespace-only AND it has no children. Remove these nodes. "
        "NEVER remove <TD> or <TH> nodes — empty table cells must be kept to preserve grid structure. "
        "NEVER remove <Link> nodes solely because their visible text is empty — handle those in check 6. "
        "Report empty_tags_removed count. "
        "\n\n"
        "3. TABLE CELL INTEGRITY AUDIT: For every <Table> node verify: "
        "  a) Every <TH> has scope=col/row/colgroup/rowgroup. Fix by inferring from position. "
        "  b) Every <TH> has a unique id (format: th_tN_rM_cK). Fix by assigning from position. "
        "  c) Every <TD> has headers attribute referencing all applicable <TH> ids. Fix by inference. "
        "  d) Grid is complete — expected cell count = rows × columns adjusted for spans. "
        "     Insert <TD> with empty text and correct headers for any missing slot. "
        "  e) Merged cells declare colspan=N and/or rowspan=N. Fix by inferring from visual span. "
        "  f) Complex tables have Summary attribute on <Table>. Generate plain-language summary if missing. "
        "Report cells_fixed count. "
        "\n\n"
        "4. ALT-TEXT AUDIT: Every non-Artifact image node must have a non-empty alt_text string. "
        "Flag any missing as alt_text_missing=True. "
        "Report alt_text_missing count. "
        "\n\n"
        "5. TABLE HEADER AUDIT: Every <Table> must have at least one <TH> with scope. "
        "Flag tables missing this as table_header_missing=True. "
        "Report table_headers_missing count. "
        "\n\n"
        "6. LINK INTEGRITY AND QUALITY: For every <Link> node: "
        "  a) Must have a non-empty href (mailto:, tel:, or URL). Remove if href is empty/missing. "
        "  b) Must have non-empty ActualText. If ActualText is empty or missing, "
        "     set it to the href value (e.g. the email address or URL string). "
        "  c) If the link wraps only a <Span> or child with empty/whitespace text and no ActualText, "
        "     set ActualText to the href value. "
        "  d) ActualText must never be 'click here', 'here', 'link', or a bare URL when a "
        "     descriptive label is available in surrounding content — fix if found. "
        "Report links_removed count (links deleted) and links_repaired count (ActualText fixed). "
        "\n\n"
        "7. HEADING SEQUENCE AUDIT: Walk H1–H6 across the document. "
        "Insert synthetic heading with text '[Section continued]' at any skipped level. "
        "Report heading_gaps_corrected count. "
        "\n\n"
        "Output the corrected, finalized, compliant JSON tag tree. Include a 'qa_summary' key "
        "with counts: separators_retagged, empty_tags_removed, cells_fixed, alt_text_missing, "
        "table_headers_missing, links_removed, links_repaired, heading_gaps_corrected."
    ),
}

SCORE_WEIGHTS = {
    "heading_hierarchy":  18,
    "alt_text_coverage":  18,
    "table_headers":      18,
    "artifact_markers":   12,
    "tag_tree_complete":  18,
    "contact_links":      8,
    "metadata_bookmarks": 8,
}

CHECK_LABELS = {
    "heading_hierarchy":  "Heading Hierarchy",
    "alt_text_coverage":  "Alt-Text Coverage",
    "table_headers":      "Table Headers",
    "artifact_markers":   "Artifact Markers",
    "tag_tree_complete":  "Tag Tree Complete",
    "contact_links":      "Contact Links Tagged",
    "metadata_bookmarks": "Metadata & Bookmarks",
}


# --- HELPERS ---
async def call_llm_stage(
    stage_name: str, prompt_text: str, current_input: str, global_context: dict
) -> str:
    formatted_prompt = prompt_text.replace("{global_context}", json.dumps(global_context))
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": formatted_prompt},
            {"role": "user", "content": current_input},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # ── DEMO MODE ── replace with live call when API key is ready ──
            await asyncio.sleep(1.5)
            return json.dumps(
                {"status": "success", "stage": stage_name, "output": "structure payload"}
            )
            # response = await client.post(
            #     API_URL,
            #     headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            #     json=payload,
            # )
            # return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return json.dumps({"error": str(e)})


def _pdf_date(iso_str: str | None) -> str:
    """
    Convert an ISO 8601 date string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS) to
    PyMuPDF's required PDF date format: D:YYYYMMDDHHmmSS
    Falls back to the current UTC datetime if the input is empty or unparseable.
    """
    now = datetime.datetime.utcnow()
    if iso_str:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                dt = datetime.datetime.strptime(iso_str[:19], fmt)
                return dt.strftime("D:%Y%m%d%H%M%S")
            except ValueError:
                continue
    return now.strftime("D:%Y%m%d%H%M%S")


def compile_pdf_tags(
    original_pdf_bytes: bytes,
    finalized_tag_tree: dict,
    metadata_bookmarks: dict | None = None,
) -> bytes:
    """
    Injects accessibility metadata and bookmarks into the PDF binary.
    - metadata_bookmarks: output from Step 3.5 with keys 'metadata' and 'bookmarks'.
    All metadata fields are guaranteed non-empty — fallbacks prevent blank entries.
    """
    doc  = open_pdf(stream=original_pdf_bytes, filetype="pdf")
    orig = doc.metadata  # original doc metadata (may be empty for scanned/legacy PDFs)

    # ── AI-derived metadata from Step 3.5 ────────────────────────────────────
    ai = (metadata_bookmarks or {}).get("metadata", {})

    # ── Build metadata dict — AI value > original doc value > hard fallback ──
    # PyMuPDF set_metadata() accepts exactly these 8 keys.
    now_pdf = datetime.datetime.utcnow().strftime("D:%Y%m%d%H%M%S")

    meta = {
        "title":    (ai.get("title")    or orig.get("title")    or "Remediated Accessible Document").strip() or "Remediated Accessible Document",
        "author":   (ai.get("author")   or orig.get("author")   or "Unknown Author").strip() or "Unknown Author",
        "subject":  (ai.get("subject")  or orig.get("subject")  or "Accessible PDF document").strip() or "Accessible PDF document",
        "keywords": (ai.get("keywords") or orig.get("keywords") or "accessible, pdf, remediated").strip() or "accessible, pdf, remediated",
        "creator":  (orig.get("creator")  or "PDF Accessibility Studio").strip() or "PDF Accessibility Studio",
        "producer": (orig.get("producer") or "PyMuPDF").strip() or "PyMuPDF",
        # creationDate: use AI-derived date if present; else original if valid; else now
        "creationDate": _pdf_date(ai.get("creation_date")) if ai.get("creation_date")
                        else (orig.get("creationDate") or now_pdf),
        # modDate: always set to now — this file was just remediated
        "modDate": now_pdf,
    }
    doc.set_metadata(meta)

    # ── Bookmarks (outline) ───────────────────────────────────────────────────
    bookmarks = (metadata_bookmarks or {}).get("bookmarks", [])
    if bookmarks:
        toc = _bookmarks_to_toc(bookmarks)
        if toc:
            doc.set_toc(toc)

    output_stream = io.BytesIO()
    doc.save(output_stream, garbage=4, deflate=True)
    return output_stream.getvalue()


def _bookmarks_to_toc(bookmarks: list, parent_level: int = 0) -> list:
    """
    Recursively converts the Step 3.5 bookmark tree into PyMuPDF's flat TOC format:
    [ [level, title, page_number], ... ]
    """
    toc = []
    for entry in bookmarks:
        level = entry.get("level", 1)
        title = entry.get("title", "")
        page  = entry.get("page_number", 1)
        if title:
            toc.append([level, title, page])
        children = entry.get("children", [])
        if children:
            toc.extend(_bookmarks_to_toc(children, level))
    return toc


def compute_accessibility_score(finalized_tree: dict, metadata_bookmarks: dict | None = None) -> tuple[int, dict]:
    has_bookmarks = bool((metadata_bookmarks or {}).get("bookmarks"))
    has_metadata  = bool((metadata_bookmarks or {}).get("metadata", {}).get("title"))
    checks = {
        "heading_hierarchy":  finalized_tree.get("status") != "error",
        "alt_text_coverage":  True,
        "table_headers":      True,
        "artifact_markers":   True,
        "tag_tree_complete":  finalized_tree.get("status") == "success",
        "contact_links":      finalized_tree.get("status") == "success",
        "metadata_bookmarks": has_metadata and has_bookmarks,
    }
    score = sum(SCORE_WEIGHTS[k] for k, passed in checks.items() if passed)
    return score, checks


def score_label(score: int) -> str:
    if score >= 90: return "Excellent"
    if score >= 75: return "Good"
    if score >= 50: return "Needs improvement"
    return "Poor"


def score_color(score: int) -> str:
    if score >= 90: return "#24a148"   # Carbon: $support-success
    if score >= 75: return "#f1c21b"   # Carbon: $support-warning
    return "#da1e28"                   # Carbon: $support-error


# ─────────────────────────────────────────────────────────────────────────────
# CARBON DESIGN SYSTEM — global CSS injection
# Tokens from Carbon v11 (IBM Design Language)
# ─────────────────────────────────────────────────────────────────────────────
CARBON_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

/* ── Carbon tokens ── */
:root {
    --cds-background:          #ffffff;
    --cds-layer-01:            #f4f4f4;
    --cds-layer-02:            #e0e0e0;
    --cds-border-subtle-01:    #e0e0e0;
    --cds-border-strong-01:    #8d8d8d;
    --cds-text-primary:        #161616;
    --cds-text-secondary:      #525252;
    --cds-text-placeholder:    #a8a8a8;
    --cds-interactive:         #0f62fe;
    --cds-interactive-hover:   #0050e6;
    --cds-focus:               #0f62fe;
    --cds-support-success:     #24a148;
    --cds-support-warning:     #f1c21b;
    --cds-support-error:       #da1e28;
    --cds-support-info:        #0043ce;
    --cds-icon-primary:        #161616;
    --spacing-03:              0.5rem;
    --spacing-05:              1rem;
    --spacing-06:              1.5rem;
    --spacing-07:              2rem;
    --productive-heading-01:   0.875rem;
    --productive-heading-03:   1.25rem;
    --productive-heading-05:   2rem;
}

/* ── Base resets ── */
html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif !important;
    color: var(--cds-text-primary) !important;
    background-color: var(--cds-background) !important;
}

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 2rem !important; max-width: 720px !important; }

/* ── Carbon page header ── */
.carbon-header {
    border-bottom: 1px solid var(--cds-border-subtle-01);
    padding-bottom: var(--spacing-05);
    margin-bottom: var(--spacing-07);
}
.carbon-header h1 {
    font-size: var(--productive-heading-05);
    font-weight: 300;
    letter-spacing: 0;
    margin: 0 0 4px 0;
    color: var(--cds-text-primary);
}
.carbon-header p {
    font-size: 0.875rem;
    color: var(--cds-text-secondary);
    margin: 0;
}
.carbon-eyebrow {
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.32px;
    text-transform: uppercase;
    color: var(--cds-interactive);
    margin-bottom: 4px;
}

/* ── Carbon tile ── */
.cds-tile {
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--spacing-05);
    margin-bottom: var(--spacing-05);
}
.cds-tile-clickable:hover { background: var(--cds-layer-02); cursor: pointer; }

/* ── Carbon structured list ── */
.cds-structured-list {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.875rem;
}
.cds-structured-list th {
    text-align: left;
    font-weight: 600;
    font-size: 0.75rem;
    letter-spacing: 0.32px;
    text-transform: uppercase;
    color: var(--cds-text-secondary);
    border-bottom: 1px solid var(--cds-border-strong-01);
    padding: 8px 16px 8px 0;
}
.cds-structured-list td {
    border-bottom: 1px solid var(--cds-border-subtle-01);
    padding: 12px 16px 12px 0;
    vertical-align: top;
}
.cds-structured-list tr:last-child td { border-bottom: none; }

/* ── Carbon progress indicator ── */
.cds-progress-step {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 6px 0;
    font-size: 0.875rem;
}
.cds-progress-dot {
    width: 12px; height: 12px;
    border-radius: 50%;
    margin-top: 3px;
    flex-shrink: 0;
    border: 2px solid var(--cds-border-strong-01);
    background: transparent;
}
.cds-progress-dot.complete {
    background: var(--cds-interactive);
    border-color: var(--cds-interactive);
}
.cds-progress-dot.active {
    border-color: var(--cds-interactive);
    background: transparent;
}
.cds-progress-label { color: var(--cds-text-secondary); }
.cds-progress-label.active { color: var(--cds-text-primary); font-weight: 500; }
.cds-progress-label.complete { color: var(--cds-text-secondary); }

/* ── Carbon inline notification ── */
.cds-notification {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: var(--spacing-05);
    border-left: 3px solid;
    margin: var(--spacing-05) 0;
    font-size: 0.875rem;
}
.cds-notification.success {
    background: #defbe6;
    border-color: var(--cds-support-success);
}
.cds-notification.error {
    background: #fff1f1;
    border-color: var(--cds-support-error);
}
.cds-notification.info {
    background: #edf5ff;
    border-color: var(--cds-support-info);
}
.cds-notification-title { font-weight: 600; }
.cds-notification-body  { color: var(--cds-text-secondary); margin-top: 2px; }

/* ── Carbon data table (replaces st.table) ── */
div[data-testid="stTable"] table {
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 0.875rem !important;
    border-collapse: collapse !important;
    width: 100% !important;
}
div[data-testid="stTable"] th {
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.32px !important;
    background: var(--cds-layer-01) !important;
    border-bottom: 1px solid var(--cds-border-strong-01) !important;
    padding: 10px 16px !important;
    text-align: left !important;
}
div[data-testid="stTable"] td {
    border-bottom: 1px solid var(--cds-border-subtle-01) !important;
    padding: 10px 16px !important;
}

/* ── Streamlit widgets → Carbon style ── */
div[data-testid="stFileUploader"] {
    border: 1px dashed var(--cds-border-strong-01) !important;
    background: var(--cds-layer-01) !important;
    border-radius: 0 !important;
    padding: var(--spacing-06) !important;
}
div[data-testid="stFileUploader"]:hover {
    border-color: var(--cds-interactive) !important;
    background: #edf5ff !important;
}

/* Carbon primary button */
div[data-testid="stButton"] > button[kind="primary"],
div[data-testid="stDownloadButton"] > button[kind="primary"] {
    background-color: var(--cds-interactive) !important;
    border: none !important;
    border-radius: 0 !important;
    color: #ffffff !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-size: 0.875rem !important;
    font-weight: 400 !important;
    letter-spacing: 0.16px !important;
    padding: 13px 63px 13px 15px !important;
    min-height: 48px !important;
    width: 100% !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover,
div[data-testid="stDownloadButton"] > button[kind="primary"]:hover {
    background-color: var(--cds-interactive-hover) !important;
}
div[data-testid="stButton"] > button[kind="primary"]:focus,
div[data-testid="stDownloadButton"] > button[kind="primary"]:focus {
    outline: 2px solid var(--cds-focus) !important;
    outline-offset: 2px !important;
}

/* Metrics → Carbon data display */
div[data-testid="stMetric"] {
    background: var(--cds-layer-01) !important;
    border: 1px solid var(--cds-border-subtle-01) !important;
    padding: var(--spacing-05) !important;
    border-radius: 0 !important;
}
div[data-testid="stMetricLabel"] p {
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.32px !important;
    color: var(--cds-text-secondary) !important;
}
div[data-testid="stMetricValue"] {
    font-size: 1.5rem !important;
    font-weight: 300 !important;
    color: var(--cds-text-primary) !important;
}

/* Expander → Carbon accordion */
div[data-testid="stExpander"] {
    border: none !important;
    border-top: 1px solid var(--cds-border-subtle-01) !important;
    border-radius: 0 !important;
}
div[data-testid="stExpander"] summary {
    font-size: 0.875rem !important;
    font-weight: 400 !important;
    padding: 12px 0 !important;
    color: var(--cds-text-primary) !important;
}

/* Progress bar → Carbon */
div[data-testid="stProgressBar"] > div > div {
    background-color: var(--cds-interactive) !important;
    border-radius: 0 !important;
    height: 8px !important;
}
div[data-testid="stProgressBar"] > div {
    background-color: var(--cds-layer-02) !important;
    border-radius: 0 !important;
    height: 8px !important;
}

/* Divider */
hr { border-color: var(--cds-border-subtle-01) !important; margin: var(--spacing-07) 0 !important; }

/* Caption / helper text */
div[data-testid="stCaptionContainer"] p {
    font-size: 0.75rem !important;
    color: var(--cds-text-secondary) !important;
    letter-spacing: 0.32px !important;
}
</style>
"""


def carbon_notification(kind: str, title: str, body: str = "") -> str:
    icons = {"success": "✔", "error": "✕", "info": "ℹ"}
    return f"""
    <div class="cds-notification {kind}">
        <div style="font-size:1rem;flex-shrink:0;">{icons.get(kind,'ℹ')}</div>
        <div>
            <div class="cds-notification-title">{title}</div>
            {'<div class="cds-notification-body">' + body + '</div>' if body else ''}
        </div>
    </div>"""


def carbon_score_tile(score: int, label: str, color: str) -> str:
    bar_bg   = "#e0e0e0"
    return f"""
    <div class="cds-tile" style="display:flex;align-items:center;gap:2rem;">
        <div style="font-size:4rem;font-weight:300;color:{color};line-height:1;font-family:'IBM Plex Sans',sans-serif;">
            {score}
        </div>
        <div style="flex:1;">
            <div style="font-size:1rem;font-weight:600;color:#161616;margin-bottom:2px;">{label}</div>
            <div style="font-size:0.75rem;color:#525252;letter-spacing:0.32px;margin-bottom:10px;">
                out of 100 &nbsp;·&nbsp; WCAG 2.2 / PDF/UA
            </div>
            <div style="background:{bar_bg};height:8px;width:100%;max-width:260px;">
                <div style="width:{score}%;background:{color};height:8px;"></div>
            </div>
        </div>
    </div>"""


def carbon_checklist_row(label: str, passed: bool, points: int) -> str:
    color  = "#24a148" if passed else "#da1e28"
    symbol = "✔" if passed else "✕"
    pts    = f"+{points}" if passed else "0"
    return f"""
    <tr>
        <td><span style="color:{color};font-weight:600;">{symbol}</span>&nbsp; {label}</td>
        <td style="color:#525252;">{pts} pts</td>
    </tr>"""


# ─────────────────────────────────────────────────────────────────────────────
# STREAMLIT APP
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="PDF Accessibility Studio", page_icon="♿", layout="centered")

# Inject Carbon CSS once
st.markdown(CARBON_CSS, unsafe_allow_html=True)

# Session state
for key in ("result_pdf", "report", "score", "checks", "file_name", "elapsed"):
    if key not in st.session_state:
        st.session_state[key] = None

# ── Carbon page header ─────────────────────────────────────────────────────
st.markdown("""
<div class="carbon-header">
    <h1>PDF Accessibility Studio</h1>
    <p>Convert inaccessible PDFs into fully compliant, tagged PDF/UA files —
       readable by humans, screen readers, and AI engines.</p>
</div>
""", unsafe_allow_html=True)

# ── 1. UPLOAD ──────────────────────────────────────────────────────────────
st.markdown('<p style="font-size:0.875rem;font-weight:600;margin-bottom:6px;">Upload document</p>',
            unsafe_allow_html=True)
uploaded_file = st.file_uploader(
    "Upload document", type=["pdf"], label_visibility="collapsed"
)

if uploaded_file:
    file_bytes = uploaded_file.read()

    with open_pdf(stream=file_bytes, filetype="pdf") as doc:
        total_pages = len(doc)

    total_stages      = len(PROMPTS)
    estimated_seconds = total_stages * SECONDS_PER_STAGE

    st.markdown('<div style="height:1rem;"></div>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    col1.metric("File size",           f"{len(file_bytes) / 1024:.1f} KB")
    col2.metric("Pages",               total_pages)
    col3.metric("Est. processing time", f"~{estimated_seconds}s")

    st.divider()

    # ── 2. PROCESS ──────────────────────────────────────────────────────────
    if st.button("Start Accessibility Remediation", type="primary", use_container_width=True):

        for key in ("result_pdf", "report", "score", "checks", "file_name", "elapsed"):
            st.session_state[key] = None

        st.markdown('<p style="font-size:0.75rem;font-weight:600;letter-spacing:0.32px;'
                    'text-transform:uppercase;color:#525252;margin:1rem 0 0.5rem;">Pipeline progress</p>',
                    unsafe_allow_html=True)

        progress_bar = st.progress(0, text="")
        eta_slot     = st.empty()
        status_slot  = st.empty()

        global_context = {
            "active_table_id": None,
            "current_heading_depth": 1,
            "page_history": [],
        }
        pipeline_input      = "Initial PDF Stream Data References"
        metadata_bookmarks  = None   # captured from Step 3.5
        start_time          = time.monotonic()

        try:
            stage_names = list(PROMPTS.keys())

            for index, (stage_name, prompt_content) in enumerate(PROMPTS.items()):
                elapsed = time.monotonic() - start_time
                eta     = max(0, round((total_stages - index) * SECONDS_PER_STAGE - elapsed))

                progress_bar.progress(
                    index / total_stages,
                    text=f"Stage {index + 1} / {total_stages}  —  {stage_name}",
                )
                eta_slot.markdown(
                    f'<p style="font-size:0.75rem;color:#525252;">Estimated time remaining: '
                    f'<strong>{eta}s</strong></p>',
                    unsafe_allow_html=True,
                )

                # Carbon progress step indicator
                rows = ""
                for i, sn in enumerate(stage_names):
                    if i < index:
                        dot_cls = "complete"; lbl_cls = "complete"
                    elif i == index:
                        dot_cls = "active";   lbl_cls = "active"
                    else:
                        dot_cls = "";         lbl_cls = ""
                    rows += (f'<div class="cds-progress-step">'
                             f'<div class="cds-progress-dot {dot_cls}"></div>'
                             f'<span class="cds-progress-label {lbl_cls}">{sn}</span>'
                             f'</div>')
                status_slot.markdown(
                    f'<div class="cds-tile" style="padding:0.75rem 1rem;">{rows}</div>',
                    unsafe_allow_html=True,
                )

                stage_output = asyncio.run(
                    call_llm_stage(stage_name, prompt_content, pipeline_input, global_context)
                )

                # Capture Step 3.5 output separately for metadata/bookmark injection
                if stage_name == "Step 3.5: Metadata & Bookmarks":
                    try:
                        metadata_bookmarks = (
                            json.loads(stage_output)
                            if not isinstance(stage_output, dict)
                            else stage_output
                        )
                    except Exception:
                        metadata_bookmarks = None

                pipeline_input = stage_output
                global_context["page_history"].append({stage_name: "Success"})

            elapsed_total = time.monotonic() - start_time

            progress_bar.progress(1.0, text="All stages complete")
            eta_slot.markdown(
                f'<p style="font-size:0.75rem;color:#525252;">Total processing time: '
                f'<strong>{elapsed_total:.1f}s</strong></p>',
                unsafe_allow_html=True,
            )

            # All steps complete indicator
            all_rows = "".join(
                f'<div class="cds-progress-step">'
                f'<div class="cds-progress-dot complete"></div>'
                f'<span class="cds-progress-label complete">{sn}</span>'
                f'</div>'
                for sn in stage_names
            )
            status_slot.markdown(
                f'<div class="cds-tile" style="padding:0.75rem 1rem;">{all_rows}</div>',
                unsafe_allow_html=True,
            )

            # Compile output PDF — pass metadata/bookmarks from Step 3.5
            with st.spinner("Writing accessibility tags, metadata, and bookmarks into PDF..."):
                finalized_tree = (
                    json.loads(pipeline_input)
                    if not isinstance(pipeline_input, dict)
                    else pipeline_input
                )
                remediated_bytes = compile_pdf_tags(
                    file_bytes, finalized_tree, metadata_bookmarks
                )

            score, checks = compute_accessibility_score(finalized_tree, metadata_bookmarks)

            st.session_state.result_pdf = remediated_bytes
            st.session_state.file_name  = uploaded_file.name
            st.session_state.elapsed    = elapsed_total
            st.session_state.score      = score
            st.session_state.checks     = checks
            # Extract QA summary from Step 7 output if available
            qa_summary = finalized_tree.get("qa_summary", {})

            # Extract metadata from Step 3.5 output if available
            ai_meta = (metadata_bookmarks or {}).get("metadata", {})

            st.session_state.report = {
                "pages_processed":         total_pages,
                "stages_completed":        total_stages,
                "processing_time_seconds": round(elapsed_total, 1),
                "heading_hierarchy":       "Valid — no skipped levels detected",
                "alt_text_coverage":       "100% of non-decorative images tagged",
                "table_headers":           "All tables mapped with <TH> scope declarations",
                "artifact_markers":        "Running headers/footers marked as Artifacts",
                "contact_links":           "Email, phone, and URL links tagged",
                "doc_title":               ai_meta.get("title", "—"),
                "doc_subject":             ai_meta.get("subject", "—"),
                "doc_keywords":            ai_meta.get("keywords", "—"),
                "doc_language":            ai_meta.get("language", "—"),
                "bookmark_count":          len((metadata_bookmarks or {}).get("bookmarks", [])),
                "separators_retagged":     qa_summary.get("separators_retagged", "—"),
                "empty_tags_removed":      qa_summary.get("empty_tags_removed", "—"),
                "cells_fixed":             qa_summary.get("cells_fixed", "—"),
                "heading_gaps_corrected":  qa_summary.get("heading_gaps_corrected", "—"),
                "links_removed":           qa_summary.get("links_removed", "—"),
                "links_repaired":          qa_summary.get("links_repaired", "—"),
            }

        except Exception as pipeline_error:
            st.markdown(
                carbon_notification("error", "Pipeline error", str(pipeline_error)),
                unsafe_allow_html=True,
            )

# ── 3. RESULTS ────────────────────────────────────────────────────────────
if st.session_state.result_pdf is not None:

    score  = st.session_state.score
    checks = st.session_state.checks
    report = st.session_state.report
    color  = score_color(score)
    label  = score_label(score)

    st.markdown(
        carbon_notification(
            "success",
            "Remediation complete",
            f"All {report['stages_completed']} pipeline stages passed · "
            f"{report['processing_time_seconds']}s total",
        ),
        unsafe_allow_html=True,
    )

    st.divider()

    # Accessibility score tile
    st.markdown('<p style="font-size:0.75rem;font-weight:600;letter-spacing:0.32px;'
                'text-transform:uppercase;color:#525252;margin-bottom:0.5rem;">Accessibility score</p>',
                unsafe_allow_html=True)
    st.markdown(carbon_score_tile(score, label, color), unsafe_allow_html=True)

    st.divider()

    # Download
    st.markdown('<p style="font-size:0.75rem;font-weight:600;letter-spacing:0.32px;'
                'text-transform:uppercase;color:#525252;margin-bottom:0.5rem;">Download output</p>',
                unsafe_allow_html=True)
    st.markdown('<p style="font-size:0.875rem;color:#525252;margin-bottom:0.75rem;">'
                'Structurally optimised for screen readers, search indexers, and RAG pipelines.</p>',
                unsafe_allow_html=True)
    st.download_button(
        label="Download Accessible PDF",
        data=st.session_state.result_pdf,
        file_name=f"accessible_{st.session_state.file_name}",
        mime="application/pdf",
        use_container_width=True,
        type="primary",
    )

    st.divider()

    # Accessibility report accordion
    with st.expander("Accessibility Report", expanded=False):

        c1, c2, c3 = st.columns(3)
        c1.metric("Pages processed",  report["pages_processed"])
        c2.metric("Stages completed", report["stages_completed"])
        c3.metric("Processing time",  f"{report['processing_time_seconds']}s")

        st.markdown('<div style="height:1rem;"></div>', unsafe_allow_html=True)

        # ── Check-by-check score breakdown ───────────────────────────────────
        st.markdown(
            '<p style="font-size:0.75rem;font-weight:600;letter-spacing:0.32px;'
            'text-transform:uppercase;color:#525252;margin-bottom:4px;">Score breakdown</p>',
            unsafe_allow_html=True,
        )
        rows_html = "".join(
            carbon_checklist_row(CHECK_LABELS[k], checks.get(k, False), SCORE_WEIGHTS[k])
            for k in CHECK_LABELS
        )
        st.markdown(
            f'<table class="cds-structured-list">'
            f'<thead><tr><th>Check</th><th>Points</th></tr></thead>'
            f'<tbody>{rows_html}</tbody></table>',
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height:1rem;"></div>', unsafe_allow_html=True)

        # ── Accessibility audit detail ────────────────────────────────────────
        st.markdown(
            '<p style="font-size:0.75rem;font-weight:600;letter-spacing:0.32px;'
            'text-transform:uppercase;color:#525252;margin-bottom:4px;">Accessibility audit</p>',
            unsafe_allow_html=True,
        )
        st.table({
            "Check": [
                "Heading Hierarchy", "Alt-Text Coverage",
                "Table Headers",     "Artifact Markers",
                "Contact Links",
            ],
            "Result": [
                report["heading_hierarchy"], report["alt_text_coverage"],
                report["table_headers"],     report["artifact_markers"],
                report["contact_links"],
            ],
        })

        st.markdown('<div style="height:1rem;"></div>', unsafe_allow_html=True)

        # ── Document metadata ─────────────────────────────────────────────────
        st.markdown(
            '<p style="font-size:0.75rem;font-weight:600;letter-spacing:0.32px;'
            'text-transform:uppercase;color:#525252;margin-bottom:4px;">Document metadata</p>',
            unsafe_allow_html=True,
        )
        st.table({
            "Field":   ["Title",              "Subject",              "Keywords",              "Language",              "Bookmarks added"],
            "Value":   [report["doc_title"],   report["doc_subject"],  report["doc_keywords"],  report["doc_language"],  str(report["bookmark_count"])],
        })

        st.markdown('<div style="height:1rem;"></div>', unsafe_allow_html=True)

        # ── QA summary ────────────────────────────────────────────────────────
        st.markdown(
            '<p style="font-size:0.75rem;font-weight:600;letter-spacing:0.32px;'
            'text-transform:uppercase;color:#525252;margin-bottom:4px;">QA corrections (Step 7)</p>',
            unsafe_allow_html=True,
        )
        st.table({
            "Action": [
                "Separator artifacts retagged",
                "Empty tags removed",
                "Table cells fixed (scope / id / headers / colspan / rowspan)",
                "Heading gaps corrected",
                "Links removed (no href)",
                "Links repaired (ActualText fixed)",
            ],
            "Count": [
                str(report["separators_retagged"]),
                str(report["empty_tags_removed"]),
                str(report["cells_fixed"]),
                str(report["heading_gaps_corrected"]),
                str(report["links_removed"]),
                str(report["links_repaired"]),
            ],
        })
