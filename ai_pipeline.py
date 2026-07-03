"""
ai_pipeline.py — Asynchronous 7-step prompt chain orchestrator.

Pipeline flow per page
----------------------
  render (pdf_processor) → PROMPT_0 (OCR) → PROMPT_1 (Layout)
  → PROMPT_2 (Zone text) → PROMPT_3 (Tag tree) → PROMPT_4 (Alt-text)
  → PROMPT_5 (Tables) → PROMPT_6 (QA/Validation)
  → inject (pdf_processor)

Cross-page state is maintained in a single GlobalContext instance that is
serialised and injected into PROMPT_3 and PROMPT_5 on every page.

Error handling (.bobrules)
--------------------------
- Every LLM call uses exponential backoff (up to MAX_RETRIES attempts).
- If Pydantic validation fails after all retries, the pipeline falls back to
  a deterministic text-only layout derived from the OcrPage tokens.
- Every WARNING or ERROR emits a PipelineError into the errors list returned
  alongside the TagTree, so the caller / UI can trigger TroubleshootingAdvisor.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from typing import Any, Optional

import httpx
from PIL import Image

from pdf_processor import image_to_bytes
from schemas import (
    GlobalContext,
    LayoutBlueprint,
    OcrPage,
    PdfTag,
    TagNode,
    TagTree,
    ZoneTextMap,
)
# Import PipelineError lazily to avoid circular imports with troubleshooter.py.
# Type-checking only — runtime import is in _emit_error().
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from troubleshooter import PipelineError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

MAX_RETRIES: int = 4
BASE_BACKOFF_SECONDS: float = 1.0
MAX_BACKOFF_SECONDS: float = 30.0

# ---------------------------------------------------------------------------
# Prompt templates (imported verbatim from 7-steps-prompt-chain-pipeline.md)
# ---------------------------------------------------------------------------

PROMPT_0 = (
    "You are an advanced multi-modal OCR engine. Process the provided raw document image page.\n"
    "Extract all visible text with 100% literal accuracy, preserving specialized characters, "
    "mathematical equations, and numeric structures.\n"
    "For every block, line, or explicit word, calculate and output its normalized bounding box "
    "coordinates (Xmin, Ymin, Xmax, Ymax) scaled from 0 to 1000.\n"
    "If text is distorted, vertically aligned, or displays broken encodings, reconstruct the "
    "correct characters contextually.\n"
    "Output format: Strict raw JSON stream containing an array of tokens with text and "
    "bounding_box parameters. Do not wrap in markdown code blocks."
)

PROMPT_1 = (
    "Analyze the provided PDF page image alongside the raw OCR coordinates from Step 0.\n"
    "Identify and map the physical layout zones. Classify each zone into: Header, Footer, "
    "Paragraph, Heading (1-6), List, Table, Image, Sidebar, or Decorative.\n"
    "CRITICAL FOR READING ORDER: If a page has two columns, ensure the zones are ordered down "
    "the first column completely before starting the second column. "
    "Do not read horizontally across columns.\n"
    "Output a structured JSON blueprint mapping zone IDs to their type and sequential reading "
    "position indices."
)

PROMPT_2 = (
    "Using the layout blueprint from Step 1, map the raw text fragments into their assigned "
    "structural zone IDs.\n"
    "Verify that sentences breaking across columns or lines are stitched together natively "
    "without artificial hyphenations or line breaks.\n"
    "Repair all corrupted unicode artifacts so text strings are fully searchable and readable "
    "by text-to-speech software.\n"
    "Output format: JSON mapping zone IDs to clean, compiled string text."
)

PROMPT_3 = (
    "Act as a certified PDF/UA compliance engineer. Take the ordered zone text from Step 2.\n"
    "Review the global heading history context: {global_context}.\n"
    "Generate a valid PDF Tag Tree structure applying semantic tags: "
    "<H1> through <H6>, <P>, <L>, <LI>, and <Link>.\n"
    "CRITICAL: Do not skip heading levels (e.g., if the previous page ended on H2, do not "
    "start this page with H4 unless an H3 is established). If an orphan title is found, "
    "logically group it based on the document layout history.\n"
    "Mark repeating running headers and footers explicitly as <Artifact> so assistive tools "
    "bypass them."
)

PROMPT_4 = (
    "Analyze all image assets on this page. Review the surrounding textual content from Step 2 "
    "to establish deep contextual relevance.\n"
    "Write highly descriptive alternative text (<Alt-Text>) optimized for accessibility. "
    "If an image contains a chart or graph, summarize the key data trends within the description.\n"
    "If an image is a spacer, logo background, or line decoration, mark it explicitly as "
    "Artifact=True.\n"
    "Append these structural properties directly into the existing Tag Tree JSON."
)

PROMPT_5 = (
    "Isolate elements tagged as a Table. Examine the historical context: {global_context}.\n"
    "Check if this table is a continuation of a table from a previous page. If yes, map this "
    "table's structure using the same column configurations.\n"
    "Accurately parse complex multi-dimensional table headers (<TH>) and map data cells (<TD>) "
    "to their parent headers.\n"
    "Handle ColSpan and RowSpan properties for split or merged cells explicitly. "
    "Never leave an isolated, unmapped cell.\n"
    "Merge this comprehensive data grid directly into the master Tag Tree structure."
)

PROMPT_6 = (
    "Act as an automated accessibility auditor. Run a final compliance pass over the completed "
    "Tag Tree against WCAG 2.2 and PDF/UA specifications.\n"
    "Check and fix the following programmatic violations:\n"
    "1. Are there any empty tags or images missing Alt-Text attributes?\n"
    "2. Are tables missing headers or scope declarations?\n"
    "3. Is the sequence of headings structurally fractured?\n"
    "Correct any anomalies natively within the tree object and output the absolute finalized, "
    "compliant JSON structure."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _encode_image(image: Image.Image) -> str:
    """Return a base64-encoded PNG string for multimodal LLM message payloads."""
    return base64.b64encode(image_to_bytes(image, fmt="PNG")).decode("utf-8")


async def _call_llm_with_retry(
    client: httpx.AsyncClient,
    api_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
) -> str:
    """
    Send a chat completion request and return the raw text response.

    Retries up to MAX_RETRIES times with exponential backoff and jitter on
    HTTP 5xx or network errors.  Raises RuntimeError if all attempts fail.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.post(api_url, headers=headers, json=payload, timeout=60.0)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError) as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"LLM call failed after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            backoff = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
            jitter = random.uniform(0, backoff * 0.2)
            wait = backoff + jitter
            logger.warning(
                "LLM attempt %d/%d failed (%s). Retrying in %.1fs.",
                attempt, MAX_RETRIES, exc, wait,
            )
            await asyncio.sleep(wait)

    raise RuntimeError("Unreachable")  # pragma: no cover


def _parse_json_strict(raw: str, context: str = "") -> Any:
    """
    Parse a JSON string from the LLM response.

    The LLM is instructed never to use markdown code fences, but we strip them
    defensively anyway (.bobrules: "No Code Blocks in AI Output").
    """
    text = raw.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` or ``` ... ```
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error{' in ' + context if context else ''}: {exc}") from exc


# ---------------------------------------------------------------------------
# Error emission helper
# ---------------------------------------------------------------------------

_STEP_NAMES = {
    0: "OCR Extraction",
    1: "Layout Analysis",
    2: "Zone Text Repair",
    3: "Semantic Tag Tree",
    4: "Image Alt-Text",
    5: "Table Matrix",
    6: "PDF/UA Validation",
}


def _emit_error(
    errors: list[Any],
    step: int,
    page_number: int,
    exc: Exception,
    raw_llm_response: Optional[str] = None,
    partial_zone_id: Optional[str] = None,
    partial_tag: Optional[str] = None,
    context: Optional[Any] = None,
    is_fallback: bool = False,
) -> None:
    """
    Build a PipelineError and append it to the errors list.
    Deferred import to avoid circular dependency with troubleshooter.py.
    """
    from troubleshooter import PipelineError  # noqa: PLC0415
    errors.append(
        PipelineError(
            step=step,
            step_name=_STEP_NAMES.get(step, f"Step {step}"),
            page_number=page_number,
            exception_message=str(exc),
            raw_llm_response=raw_llm_response,
            partial_zone_id=partial_zone_id,
            partial_tag=partial_tag,
            context_snapshot=context.model_dump() if context else None,
            is_fallback=is_fallback,
        )
    )


def _deterministic_fallback(ocr_page: OcrPage) -> TagTree:
    """
    Build a minimal TagTree from raw OCR tokens when LLM validation fails.

    Each token becomes a <P> node. This guarantees the pipeline never
    produces an empty page even on total LLM failure.
    """
    logger.warning(
        "Falling back to deterministic tag tree for page %d.", ocr_page.page_number
    )
    nodes = [
        TagNode(tag=PdfTag.P, content=token.text)
        for token in ocr_page.tokens
        if token.text.strip()
    ]
    return TagTree(page_number=ocr_page.page_number, nodes=nodes)


def _update_context_from_tree(context: GlobalContext, tree: TagTree) -> None:
    """
    Mutate GlobalContext in-place after processing a page.

    Tracks the deepest heading level seen and whether a table is still open.
    """
    heading_map = {
        PdfTag.H1: 1, PdfTag.H2: 2, PdfTag.H3: 3,
        PdfTag.H4: 4, PdfTag.H5: 5, PdfTag.H6: 6,
    }
    open_table: Optional[str] = None

    def _walk(node: TagNode) -> None:
        nonlocal open_table
        if node.tag in heading_map:
            depth = heading_map[node.tag]
            context.current_heading_depth = depth
            context.record_heading(depth)  # uses rolling-window method (Fix #3)
        if node.tag == PdfTag.TABLE and node.zone_id:
            open_table = node.zone_id
        for child in node.children:
            _walk(child)

    for root_node in tree.nodes:
        _walk(root_node)

    # A table is only "active" (cross-page) if we ended the page inside one.
    # Heuristic: if the last root-level node is a Table, treat it as open.
    if tree.nodes and tree.nodes[-1].tag == PdfTag.TABLE:
        context.active_table_id = tree.nodes[-1].zone_id
    else:
        context.active_table_id = None
        context.active_table_column_count = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def process_page(
    page_number: int,
    image: Image.Image,
    context: GlobalContext,
    client: httpx.AsyncClient,
    api_url: str,
    api_key: str,
    model: str,
) -> tuple[TagTree, list[Any]]:
    """
    Run the full 7-step prompt chain for a single page image.

    Returns
    -------
    tuple[TagTree, list[PipelineError]]
        The validated tag structure for this page, plus a (possibly empty) list
        of PipelineError objects for any steps that warned or errored. The caller
        passes these to TroubleshootingAdvisor to generate user-facing diagnostics.
    """
    errors: list[Any] = []  # list[PipelineError] — Any avoids circular import at runtime

    b64_image = _encode_image(image)
    image_message: dict[str, Any] = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}},
        ],
    }

    # ── PROMPT_0: OCR extraction ──────────────────────────────────────────────
    logger.info("Page %d — PROMPT_0: OCR extraction.", page_number)
    ocr_raw = await _call_llm_with_retry(
        client, api_url, api_key, model,
        messages=[{"role": "system", "content": PROMPT_0}, image_message],
    )
    try:
        ocr_data = _parse_json_strict(ocr_raw, "PROMPT_0")
        # Normalise: LLM may return {"tokens": [...]} or a bare list.
        if isinstance(ocr_data, dict):
            ocr_data = ocr_data.get("tokens", [])
        ocr_page = OcrPage(page_number=page_number, tokens=ocr_data)
    except (ValueError, Exception) as exc:
        logger.error("Page %d — OCR schema validation failed: %s", page_number, exc)
        _emit_error(errors, 0, page_number, exc, raw_llm_response=ocr_raw,
                    context=context, is_fallback=True)
        fallback = _deterministic_fallback(OcrPage(page_number=page_number, tokens=[]))
        return fallback, errors

    # ── PROMPT_1: Layout analysis ─────────────────────────────────────────────
    logger.info("Page %d — PROMPT_1: Layout analysis.", page_number)
    layout_raw = await _call_llm_with_retry(
        client, api_url, api_key, model,
        messages=[
            {"role": "system", "content": PROMPT_1},
            image_message,
            {"role": "user", "content": f"OCR tokens:\n{ocr_raw}"},
        ],
    )
    try:
        layout_data = _parse_json_strict(layout_raw, "PROMPT_1")
        blueprint = LayoutBlueprint(page_number=page_number, zones=layout_data)
    except (ValueError, Exception) as exc:
        logger.warning("Page %d — Layout schema invalid (%s). Continuing with empty blueprint.", page_number, exc)
        _emit_error(errors, 1, page_number, exc, raw_llm_response=layout_raw, context=context)
        blueprint = LayoutBlueprint(page_number=page_number, zones=[])

    # ── PROMPT_2: Text-to-zone anchoring ─────────────────────────────────────
    logger.info("Page %d — PROMPT_2: Zone text anchoring.", page_number)
    zone_raw = await _call_llm_with_retry(
        client, api_url, api_key, model,
        messages=[
            {"role": "system", "content": PROMPT_2},
            {"role": "user", "content": (
                f"Layout blueprint:\n{layout_raw}\n\n"
                f"OCR tokens:\n{ocr_raw}"
            )},
        ],
    )
    try:
        zone_data = _parse_json_strict(zone_raw, "PROMPT_2")
        zone_map = ZoneTextMap(page_number=page_number, zones=zone_data)
    except (ValueError, Exception) as exc:
        logger.warning("Page %d — Zone text schema invalid (%s). Using OCR fallback.", page_number, exc)
        _emit_error(errors, 2, page_number, exc, raw_llm_response=zone_raw, context=context)
        zone_map = ZoneTextMap(page_number=page_number, zones=[])

    # ── PROMPT_3: Semantic tag tree ───────────────────────────────────────────
    logger.info("Page %d — PROMPT_3: Semantic tag tree.", page_number)
    context_json = context.model_dump_json(indent=2)
    prompt3_filled = PROMPT_3.format(global_context=context_json)
    tag_raw = await _call_llm_with_retry(
        client, api_url, api_key, model,
        messages=[
            {"role": "system", "content": prompt3_filled},
            {"role": "user", "content": f"Anchored zone text:\n{zone_raw}"},
        ],
    )
    try:
        tag_data = _parse_json_strict(tag_raw, "PROMPT_3")
        tag_tree = TagTree(page_number=page_number, nodes=tag_data)
    except (ValueError, Exception) as exc:
        logger.error("Page %d — Tag tree validation failed (%s). Falling back.", page_number, exc)
        _emit_error(errors, 3, page_number, exc, raw_llm_response=tag_raw,
                    context=context, is_fallback=True)
        fallback = _deterministic_fallback(ocr_page)
        return fallback, errors

    # ── PROMPT_4: Image alt-text ──────────────────────────────────────────────
    # alt_raw initialised to tag_raw so PROMPT_5 always has a valid reference (Fix #2).
    alt_raw = tag_raw
    logger.info("Page %d — PROMPT_4: Image alt-text.", page_number)
    try:
        alt_raw = await _call_llm_with_retry(
            client, api_url, api_key, model,
            messages=[
                {"role": "system", "content": PROMPT_4},
                image_message,
                {"role": "user", "content": (
                    f"Zone text:\n{zone_raw}\n\n"
                    f"Current tag tree:\n{tag_raw}"
                )},
            ],
        )
        alt_data = _parse_json_strict(alt_raw, "PROMPT_4")
        tag_tree = TagTree(page_number=page_number, nodes=alt_data)
    except (ValueError, Exception) as exc:
        logger.warning("Page %d — Alt-text merge failed (%s). Keeping tag tree without alt-text.", page_number, exc)
        _emit_error(errors, 4, page_number, exc, raw_llm_response=alt_raw, context=context)
        alt_raw = tag_raw

    # ── PROMPT_5: Table matrix ────────────────────────────────────────────────
    logger.info("Page %d — PROMPT_5: Table matrix.", page_number)
    # Re-serialise so PROMPT_5 sees PROMPT_3's heading/table state updates (Fix #2).
    context_json = context.model_dump_json(indent=2)
    prompt5_filled = PROMPT_5.format(global_context=context_json)
    table_raw = await _call_llm_with_retry(
        client, api_url, api_key, model,
        messages=[
            {"role": "system", "content": prompt5_filled},
            {"role": "user", "content": f"Current tag tree:\n{alt_raw}"},
        ],
    )
    try:
        table_data = _parse_json_strict(table_raw, "PROMPT_5")
        tag_tree = TagTree(page_number=page_number, nodes=table_data)
    except (ValueError, Exception) as exc:
        logger.warning("Page %d — Table merge failed (%s). Skipping table enrichment.", page_number, exc)
        _emit_error(errors, 5, page_number, exc, raw_llm_response=table_raw, context=context)

    # ── PROMPT_6: QA / PDF/UA validation ─────────────────────────────────────
    logger.info("Page %d — PROMPT_6: QA validation pass.", page_number)
    qa_raw = await _call_llm_with_retry(
        client, api_url, api_key, model,
        messages=[
            {"role": "system", "content": PROMPT_6},
            {"role": "user", "content": f"Tag tree to validate:\n{table_raw}"},
        ],
    )
    try:
        qa_data = _parse_json_strict(qa_raw, "PROMPT_6")
        tag_tree = TagTree(page_number=page_number, nodes=qa_data)
    except (ValueError, Exception) as exc:
        logger.error("Page %d — Final QA validation failed (%s). Returning pre-QA tree.", page_number, exc)
        _emit_error(errors, 6, page_number, exc, raw_llm_response=qa_raw, context=context)

    # Update cross-page state for the next page.
    _update_context_from_tree(context, tag_tree)
    logger.info("Page %d — Complete. heading_depth=%d, errors=%d",
                page_number, context.current_heading_depth, len(errors))
    return tag_tree, errors


async def run_pipeline(
    pdf_bytes: bytes,
    images: list[Image.Image],
    api_url: str,
    api_key: str,
    model: str,
) -> tuple[list[TagTree], list[Any]]:
    """
    Run the full pipeline over every page of a document sequentially.

    Pages must be processed in order because GlobalContext carries forward state
    (heading depth, open table) from page N to page N+1.

    Returns
    -------
    tuple[list[TagTree], list[PipelineError]]
        All page tag trees + a flat list of every PipelineError across all pages.
        Errors carry page_number and step so the UI can attach them to the right
        ProgressStep and show a per-step "Troubleshoot" button.
    """
    context = GlobalContext()
    results: list[TagTree] = []
    all_errors: list[Any] = []

    # Connection-pooled async client, shared across all pages and all steps.
    async with httpx.AsyncClient(http2=True) as client:
        for page_number, image in enumerate(images, start=1):
            tree, page_errors = await process_page(
                page_number=page_number,
                image=image,
                context=context,
                client=client,
                api_url=api_url,
                api_key=api_key,
                model=model,
            )
            results.append(tree)
            all_errors.extend(page_errors)

    return results, all_errors
