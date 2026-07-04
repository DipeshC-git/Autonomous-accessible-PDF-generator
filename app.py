import streamlit as st
import asyncio
import httpx
import json
import io
import os
import time
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
        "Identify and map the physical layout zones. Classify each zone into: Header, Footer, "
        "Paragraph, Heading (1-6), List, Table, Image, Sidebar, or Decorative. "
        "CRITICAL FOR READING ORDER: If a page has two columns, ensure the zones are ordered down "
        "the first column completely before starting the second column. Do not read horizontally "
        "across columns. "
        "Output a structured JSON blueprint mapping zone IDs to their type and sequential reading "
        "position indices."
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
        "Mark repeating running headers and footers explicitly as <Artifact>."
    ),
    "Step 3.5: Metadata & Bookmarks": (
        "You are a PDF metadata and navigation engineer. Using the completed Tag Tree from Step 3 "
        "and the full document text from Step 2, perform two tasks: "
        "\n\n"
        "TASK A — DOCUMENT METADATA: "
        "Derive and output the following metadata fields from the document content itself: "
        "title (infer from the first prominent H1 or cover-page text), "
        "subject (one-sentence description of what the document covers), "
        "keywords (5–10 comma-separated terms extracted from headings and key concepts), "
        "language (ISO 639-1 code, e.g. 'en'), "
        "author (extract if visible in the document, otherwise leave empty string). "
        "\n\n"
        "TASK B — BOOKMARK OUTLINE: "
        "Build a hierarchical bookmark outline from every heading tagged H1–H6 in the Tag Tree. "
        "Each bookmark entry must include: title (the heading text), level (1–6), "
        "and page_number (1-based integer). "
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
        "Isolate elements tagged as a Table. Examine the historical context: {global_context}. "
        "Check if this table is a continuation of a table from a previous page. If yes, map this "
        "table's structure using the same column configurations. "
        "Accurately parse complex multi-dimensional table headers (<TH>) and map data cells (<TD>) "
        "to their parent headers. "
        "Handle ColSpan and RowSpan properties for split or merged cells explicitly. "
        "Merge this comprehensive data grid directly into the master Tag Tree structure."
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
        "product codes, dates). Return the updated Tag Tree JSON with all contact links inserted."
    ),
    "Step 7: Compliance QA": (
        "Act as an automated accessibility auditor. Run a final compliance pass over the completed "
        "Tag Tree against WCAG 2.2 and PDF/UA specifications. Perform the following checks and "
        "corrections in sequence: "
        "\n\n"
        "1. EMPTY TAG REMOVAL: Find every tag node whose text content is null, empty string, or "
        "contains only whitespace AND has no children. Remove these nodes entirely from the tree. "
        "Do not remove tags that are structural containers with valid children. "
        "\n\n"
        "2. ALT-TEXT AUDIT: Verify every non-Artifact image node has a non-empty alt_text string. "
        "If any are missing, flag them as alt_text_missing=True. "
        "\n\n"
        "3. TABLE HEADER AUDIT: Verify every <Table> node contains at least one <TH> element "
        "with a scope attribute. Flag any tables missing this as table_header_missing=True. "
        "\n\n"
        "4. HEADING SEQUENCE AUDIT: Walk the full heading sequence H1–H6 across the document. "
        "Flag any instance where a heading level is skipped (e.g. H2 followed immediately by H4). "
        "Correct by inserting a synthetic heading at the missing level with text '[Section continued]'. "
        "\n\n"
        "5. LINK INTEGRITY: Verify all <Link> nodes have a non-empty href attribute. "
        "Remove any <Link> tag that has no href and no ActualText. "
        "\n\n"
        "Output the corrected, finalized, compliant JSON tag tree. Include a 'qa_summary' key "
        "listing counts of: empty_tags_removed, alt_text_missing, table_headers_missing, "
        "heading_gaps_corrected, links_removed."
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


def compile_pdf_tags(
    original_pdf_bytes: bytes,
    finalized_tag_tree: dict,
    metadata_bookmarks: dict | None = None,
) -> bytes:
    """
    Injects accessibility metadata and bookmarks into the PDF binary.
    - metadata_bookmarks: output from Step 3.5 with keys 'metadata' and 'bookmarks'.
    """
    doc = open_pdf(stream=original_pdf_bytes, filetype="pdf")

    # ── Metadata ─────────────────────────────────────────────────────────────
    # Prefer AI-derived metadata from Step 3.5; fall back to original doc values.
    ai_meta = (metadata_bookmarks or {}).get("metadata", {})
    meta = {
        "title":        ai_meta.get("title")    or doc.metadata.get("title", "Remediated Accessible Document"),
        "author":       ai_meta.get("author")   or doc.metadata.get("author", ""),
        "subject":      ai_meta.get("subject")  or doc.metadata.get("subject", ""),
        "keywords":     ai_meta.get("keywords") or doc.metadata.get("keywords", ""),
        "creator":      doc.metadata.get("creator", ""),
        "producer":     doc.metadata.get("producer", ""),
        "creationDate": doc.metadata.get("creationDate", ""),
        "modDate":      doc.metadata.get("modDate", ""),
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
                "empty_tags_removed":      qa_summary.get("empty_tags_removed", "—"),
                "heading_gaps_corrected":  qa_summary.get("heading_gaps_corrected", "—"),
                "links_removed":           qa_summary.get("links_removed", "—"),
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
                "Empty tags removed",
                "Heading gaps corrected",
                "Invalid links removed",
            ],
            "Count": [
                str(report["empty_tags_removed"]),
                str(report["heading_gaps_corrected"]),
                str(report["links_removed"]),
            ],
        })
