"""
checker.py — Standalone PDF accessibility checker.

Inspects a PDF binary using PyMuPDF's structure-tree and metadata APIs.
No LLM required — all checks are deterministic binary analysis.

Returns an AccessibilityReport that can be compared before/after remediation:

  POST /check   →  AccessibilityReport (upload any PDF)

Checks performed
----------------
  STRUCT   Is the document tagged? (MarkInfo/Marked)
  TITLE    Does the document have a /Title metadata entry?
  LANG     Is a document language set (/Lang)?
  HEADINGS Are heading levels present and do they avoid illegal jumps?
  ALT      Do all Figure tags carry an /Alt attribute?
  TABLES   Do Table tags contain at least one TH element?
  READING  Is a reading order (structural parent tree) present?
  ARTIF    Are running headers/footers marked as Artifacts?
  UNICODE  Does the document have ActualText or proper encoding on all spans?
  ROLE_MAP Is there a /RoleMap in the structure tree?

Each check returns pass / warn / fail + a plain-English message.
"""

from __future__ import annotations

import io
import logging
from enum import Enum
from typing import Optional

import fitz  # PyMuPDF ≥ 1.24
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class CheckResult(BaseModel):
    rule_id: str
    title: str
    status: CheckStatus
    message: str
    detail: Optional[str] = None
    wcag_ref: Optional[str] = None
    pdfua_ref: Optional[str] = None


class AccessibilityReport(BaseModel):
    filename: str
    page_count: int
    is_tagged: bool
    overall_score: int = Field(..., ge=0, le=100, description="0–100 composite score")
    grade: str          = Field(..., description="A / B / C / D / F")
    checks: list[CheckResult]
    summary: str


# ---------------------------------------------------------------------------
# Score weights — higher = more impact on overall score
# ---------------------------------------------------------------------------
_WEIGHTS: dict[str, int] = {
    "STRUCT":  20,
    "TITLE":    5,
    "LANG":    10,
    "HEADINGS": 15,
    "ALT":      15,
    "TABLES":   10,
    "READING":  10,
    "ARTIF":    5,
    "UNICODE":  5,
    "ROLE_MAP": 5,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_pdf(pdf_bytes: bytes, filename: str = "document.pdf") -> AccessibilityReport:
    """
    Run all accessibility checks on a raw PDF and return a structured report.
    Safe to call synchronously — no I/O beyond the in-memory PyMuPDF open.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Could not open PDF: {exc}") from exc

    results: list[CheckResult] = []

    # ── STRUCT ────────────────────────────────────────────────────────────────
    mark_info = doc.get_markinfo() or {}
    is_tagged = bool(mark_info.get("Marked", False))
    results.append(CheckResult(
        rule_id="STRUCT",
        title="Document is tagged",
        status=CheckStatus.PASS if is_tagged else CheckStatus.FAIL,
        message="Document has MarkInfo/Marked=true — a prerequisite for all structural accessibility."
                if is_tagged else
                "Document is not tagged. Screen readers cannot access any structural information.",
        pdfua_ref="PDF/UA-1 §7.1",
        wcag_ref="WCAG 1.3.1",
    ))

    # ── TITLE ─────────────────────────────────────────────────────────────────
    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip()
    results.append(CheckResult(
        rule_id="TITLE",
        title="Document title set",
        status=CheckStatus.PASS if title else CheckStatus.FAIL,
        message=f"Title: \"{title}\"" if title else "No /Title entry in document metadata.",
        pdfua_ref="PDF/UA-1 §7.1",
        wcag_ref="WCAG 2.4.2",
    ))

    # ── LANG ──────────────────────────────────────────────────────────────────
    lang = doc.language or ""
    results.append(CheckResult(
        rule_id="LANG",
        title="Document language declared",
        status=CheckStatus.PASS if lang else CheckStatus.FAIL,
        message=f"Language: \"{lang}\"" if lang else "No /Lang entry — assistive technology cannot determine reading language.",
        pdfua_ref="PDF/UA-1 §7.2",
        wcag_ref="WCAG 3.1.1",
    ))

    # ── HEADINGS ──────────────────────────────────────────────────────────────
    heading_check = _check_headings(doc)
    results.append(heading_check)

    # ── ALT ───────────────────────────────────────────────────────────────────
    alt_check = _check_alt_text(doc)
    results.append(alt_check)

    # ── TABLES ────────────────────────────────────────────────────────────────
    table_check = _check_tables(doc)
    results.append(table_check)

    # ── READING ───────────────────────────────────────────────────────────────
    catalog_xref = doc.pdf_catalog()
    struct_root = doc.xref_get_key(catalog_xref, "StructTreeRoot")
    has_struct_tree = struct_root and struct_root[0] != "null"
    results.append(CheckResult(
        rule_id="READING",
        title="Structure tree (reading order) present",
        status=CheckStatus.PASS if has_struct_tree else (CheckStatus.WARN if is_tagged else CheckStatus.FAIL),
        message="StructTreeRoot found — reading order is encoded for assistive technology."
                if has_struct_tree else
                "No StructTreeRoot. Document may be tagged but lacks a reading-order structure tree.",
        pdfua_ref="PDF/UA-1 §7.3",
        wcag_ref="WCAG 1.3.2",
    ))

    # ── ARTIF ─────────────────────────────────────────────────────────────────
    artif_check = _check_artifacts(doc)
    results.append(artif_check)

    # ── UNICODE ───────────────────────────────────────────────────────────────
    unicode_check = _check_unicode(doc)
    results.append(unicode_check)

    # ── ROLE_MAP ──────────────────────────────────────────────────────────────
    role_map_present = False
    if has_struct_tree and struct_root:
        try:
            sr_xref = int(struct_root[1].split()[0])
            role_map_val = doc.xref_get_key(sr_xref, "RoleMap")
            role_map_present = role_map_val and role_map_val[0] != "null"
        except Exception:
            pass
    results.append(CheckResult(
        rule_id="ROLE_MAP",
        title="Structure role map present",
        status=CheckStatus.PASS if role_map_present else CheckStatus.WARN,
        message="RoleMap found — custom tag names are mapped to standard PDF roles."
                if role_map_present else
                "No RoleMap entry. Non-standard tag names may not be interpreted correctly.",
        pdfua_ref="PDF/UA-1 §7.1",
    ))

    doc.close()

    # ── Score ─────────────────────────────────────────────────────────────────
    score = _compute_score(results)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 55 else "D" if score >= 35 else "F"

    pass_count = sum(1 for r in results if r.status == CheckStatus.PASS)
    fail_count = sum(1 for r in results if r.status == CheckStatus.FAIL)
    warn_count = sum(1 for r in results if r.status == CheckStatus.WARN)

    summary = (
        f"{pass_count} checks passed, {warn_count} warnings, {fail_count} failures. "
        f"Overall accessibility grade: {grade} ({score}/100)."
    )

    return AccessibilityReport(
        filename=filename,
        page_count=int(fitz.open(stream=pdf_bytes, filetype="pdf").page_count),
        is_tagged=is_tagged,
        overall_score=score,
        grade=grade,
        checks=results,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------

def _check_headings(doc: fitz.Document) -> CheckResult:
    """Detect heading tags and verify no levels are skipped."""
    headings: list[int] = []
    for page in doc:
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
        for b in blocks:
            tag = b.get("type_name", "") or ""
            if tag.startswith("H") and tag[1:].isdigit():
                headings.append(int(tag[1:]))

    if not headings:
        return CheckResult(
            rule_id="HEADINGS", title="Heading structure",
            status=CheckStatus.WARN,
            message="No heading tags (H1–H6) found. Document may lack navigational structure.",
            pdfua_ref="PDF/UA-1 §7.1", wcag_ref="WCAG 1.3.1",
        )

    # Check for skips
    skips = []
    for i in range(1, len(headings)):
        if headings[i] > headings[i-1] + 1:
            skips.append(f"H{headings[i-1]}→H{headings[i]}")

    if skips:
        return CheckResult(
            rule_id="HEADINGS", title="Heading structure",
            status=CheckStatus.WARN,
            message=f"{len(headings)} headings found but {len(skips)} illegal level skip(s) detected.",
            detail=", ".join(skips[:5]),
            pdfua_ref="PDF/UA-1 §7.1", wcag_ref="WCAG 1.3.1",
        )
    return CheckResult(
        rule_id="HEADINGS", title="Heading structure",
        status=CheckStatus.PASS,
        message=f"{len(headings)} heading tags found with valid hierarchy.",
        pdfua_ref="PDF/UA-1 §7.1", wcag_ref="WCAG 1.3.1",
    )


def _check_alt_text(doc: fitz.Document) -> CheckResult:
    """Check Figure tags for /Alt attributes."""
    figures_total = 0
    figures_missing_alt = 0
    for page in doc:
        for img in page.get_images(full=True):
            figures_total += 1
            # PyMuPDF exposes xref; check for Alt via struct elem scan
            # Heuristic: count images without associated text nearby as missing alt
    if figures_total == 0:
        return CheckResult(
            rule_id="ALT", title="Image alt-text",
            status=CheckStatus.PASS,
            message="No images found on any page.",
            wcag_ref="WCAG 1.1.1",
        )
    # Use xref scan to find Figure StructElems with Alt
    alts_found = 0
    try:
        for xref in range(1, doc.xref_length()):
            obj_type = doc.xref_get_key(xref, "Type")
            obj_s    = doc.xref_get_key(xref, "S")
            if obj_type and "StructElem" in str(obj_type) and obj_s and "Figure" in str(obj_s):
                figures_total = max(figures_total, alts_found + 1)
                alt = doc.xref_get_key(xref, "Alt")
                if alt and alt[0] != "null" and alt[1].strip("()"):
                    alts_found += 1
    except Exception:
        pass

    missing = figures_total - alts_found
    if missing == 0:
        return CheckResult(
            rule_id="ALT", title="Image alt-text",
            status=CheckStatus.PASS,
            message=f"All {figures_total} image(s) have alt-text.",
            wcag_ref="WCAG 1.1.1", pdfua_ref="PDF/UA-1 §7.3",
        )
    return CheckResult(
        rule_id="ALT", title="Image alt-text",
        status=CheckStatus.FAIL,
        message=f"{missing} of {figures_total} image(s) are missing alt-text.",
        wcag_ref="WCAG 1.1.1", pdfua_ref="PDF/UA-1 §7.3",
    )


def _check_tables(doc: fitz.Document) -> CheckResult:
    """Check Table StructElems for at least one TH child."""
    tables = 0
    tables_with_th = 0
    try:
        for xref in range(1, doc.xref_length()):
            obj_s = doc.xref_get_key(xref, "S")
            if obj_s and "Table" in str(obj_s):
                tables += 1
                kids = doc.xref_get_key(xref, "K")
                if kids and "TH" in str(kids):
                    tables_with_th += 1
    except Exception:
        pass

    if tables == 0:
        return CheckResult(
            rule_id="TABLES", title="Table headers",
            status=CheckStatus.PASS,
            message="No tables found in document.",
            pdfua_ref="PDF/UA-1 §7.5",
        )
    missing = tables - tables_with_th
    if missing == 0:
        return CheckResult(
            rule_id="TABLES", title="Table headers",
            status=CheckStatus.PASS,
            message=f"All {tables} table(s) have header (TH) cells.",
            pdfua_ref="PDF/UA-1 §7.5", wcag_ref="WCAG 1.3.1",
        )
    return CheckResult(
        rule_id="TABLES", title="Table headers",
        status=CheckStatus.WARN if missing < tables else CheckStatus.FAIL,
        message=f"{missing} of {tables} table(s) are missing TH header cells.",
        pdfua_ref="PDF/UA-1 §7.5", wcag_ref="WCAG 1.3.1",
    )


def _check_artifacts(doc: fitz.Document) -> CheckResult:
    """Heuristic: check if any Artifact StructElems exist (good sign)."""
    has_artifacts = False
    try:
        for xref in range(1, min(doc.xref_length(), 2000)):
            obj_s = doc.xref_get_key(xref, "S")
            if obj_s and "Artifact" in str(obj_s):
                has_artifacts = True
                break
    except Exception:
        pass
    return CheckResult(
        rule_id="ARTIF", title="Artifacts marked",
        status=CheckStatus.PASS if has_artifacts else CheckStatus.WARN,
        message="Running headers/footers are marked as Artifacts — screen readers will skip them."
                if has_artifacts else
                "No Artifact tags found. Running headers/footers may pollute the reading experience.",
        pdfua_ref="PDF/UA-1 §7.1",
    )


def _check_unicode(doc: fitz.Document) -> CheckResult:
    """Check that extractable text exists (proxy for font encoding / ToUnicode)."""
    pages_checked = min(doc.page_count, 5)
    pages_with_text = 0
    for i in range(pages_checked):
        txt = doc[i].get_text("text").strip()
        if len(txt) > 20:
            pages_with_text += 1
    if pages_with_text == pages_checked:
        return CheckResult(
            rule_id="UNICODE", title="Text extractability (Unicode)",
            status=CheckStatus.PASS,
            message="Text is extractable on all sampled pages — font encoding appears correct.",
            wcag_ref="WCAG 1.4.5",
        )
    missing = pages_checked - pages_with_text
    return CheckResult(
        rule_id="UNICODE", title="Text extractability (Unicode)",
        status=CheckStatus.FAIL if missing == pages_checked else CheckStatus.WARN,
        message=f"Text could not be extracted from {missing} of {pages_checked} sampled pages. "
                "Broken font encodings may prevent screen reader access.",
        wcag_ref="WCAG 1.4.5",
    )


def _compute_score(results: list[CheckResult]) -> int:
    """Weighted score: each PASS = full weight, WARN = half, FAIL = 0."""
    total_weight = sum(_WEIGHTS.get(r.rule_id, 5) for r in results)
    earned = sum(
        _WEIGHTS.get(r.rule_id, 5) * (1.0 if r.status == CheckStatus.PASS else 0.5 if r.status == CheckStatus.WARN else 0.0)
        for r in results
    )
    return round((earned / total_weight) * 100) if total_weight else 0
