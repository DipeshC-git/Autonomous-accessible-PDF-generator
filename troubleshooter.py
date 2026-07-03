"""
troubleshooter.py — LLM-powered per-error diagnostic advisor.

How it works
------------
When the pipeline emits a WARNING or ERROR at any step, the caller creates a
PipelineError describing the failure (step name, page number, exception, and
whatever partial data is available). They then call:

    diagnosis = await TroubleshootingAdvisor(client, api_url, api_key, model).diagnose(error)

The advisor:
  1. Classifies the error into one of three categories: zone, tag, or ingestion.
  2. Generates a set of targeted clarifying questions for the user (1–3 questions
     specific to the failure context).
  3. Accepts the user's answers and produces a concrete resolution plan.

The questions and resolution are returned as structured Pydantic models so the
front-end can render them as a Carbon InlineNotification + expandable detail
panel with a "Get Help" / "Troubleshoot" button.

Periodic accuracy checks
------------------------
The module also exposes run_accuracy_check(), which can be called after every
page (or on a schedule) to ask the LLM to self-audit the last TagTree output
against PDF/UA rules and return a per-rule confidence score.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from ai_pipeline import _call_llm_with_retry, _parse_json_strict
from schemas import TagTree, GlobalContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class ErrorCategory(str, Enum):
    ZONE       = "zone"        # layout zone detection / reading-order failure
    TAG        = "tag"         # semantic tagging / heading hierarchy failure
    INGESTION  = "ingestion"   # PDF binary parsing / OCR extraction failure


class PipelineError(BaseModel):
    """
    Structured description of a pipeline failure, created by ai_pipeline.py
    whenever a step logs a WARNING or ERROR.
    """
    step: int = Field(..., ge=0, le=6, description="Prompt step index (0–6)")
    step_name: str = Field(..., description="Human-readable step label, e.g. 'OCR Extraction'")
    page_number: int = Field(..., ge=1)
    exception_message: str = Field(..., description="str(exc) from the caught exception")
    raw_llm_response: Optional[str] = Field(
        None, description="Raw LLM output that failed validation, if available"
    )
    partial_zone_id: Optional[str] = Field(
        None, description="Zone ID being processed when the error occurred"
    )
    partial_tag: Optional[str] = Field(
        None, description="PdfTag value being written when the error occurred"
    )
    context_snapshot: Optional[dict[str, Any]] = Field(
        None, description="Serialised GlobalContext at the time of failure"
    )
    is_fallback: bool = Field(
        False,
        description="True if the pipeline already fell back to deterministic mode",
    )


# ---------------------------------------------------------------------------
# Diagnosis output models
# ---------------------------------------------------------------------------

class ClarifyingQuestion(BaseModel):
    """A single targeted question to ask the user about the failure."""
    question_id: str = Field(..., description="Stable slug, e.g. 'q_zone_column_count'")
    question: str = Field(..., description="Natural-language question displayed to the user")
    category: ErrorCategory
    # Optional structured choices — if provided, the UI renders a radio group.
    choices: list[str] = Field(
        default_factory=list,
        description="Suggested answers; empty list = free-text input",
    )


class ResolutionStep(BaseModel):
    """One concrete action the system or user should take to resolve the error."""
    action: str = Field(..., description="What to do")
    target: str = Field(..., description="Which component/file/setting to change")
    rationale: str = Field(..., description="Why this resolves the issue")
    auto_applicable: bool = Field(
        False,
        description="True if the system can apply this fix without user input",
    )


class TroubleshootingDiagnosis(BaseModel):
    """
    Full diagnosis returned by TroubleshootingAdvisor.diagnose().
    Serialised and sent to the front-end to populate the troubleshooting panel.
    """
    error: PipelineError
    category: ErrorCategory
    severity: str = Field(..., description="'warning' | 'error' | 'critical'")
    summary: str = Field(..., description="One-sentence plain-English diagnosis")
    questions: list[ClarifyingQuestion] = Field(
        default_factory=list,
        description="Questions to present to the user before resolution",
    )
    # Populated after user answers questions (second advisor call).
    resolution: list[ResolutionStep] = Field(default_factory=list)
    confidence: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Advisor confidence in the diagnosis (0–1)",
    )


# ---------------------------------------------------------------------------
# Accuracy check models
# ---------------------------------------------------------------------------

class RuleCheckResult(BaseModel):
    """Result of checking one PDF/UA or WCAG rule against a TagTree."""
    rule_id: str = Field(..., description="e.g. 'PDF-UA-1/7.1' or 'WCAG-2.2/1.3.1'")
    rule_description: str
    passed: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    issue: Optional[str] = Field(None, description="Description of violation if not passed")
    suggested_fix: Optional[str] = Field(None)


class AccuracyReport(BaseModel):
    """Periodic accuracy snapshot for one page's TagTree."""
    page_number: int
    overall_score: float = Field(..., ge=0.0, le=1.0, description="Mean confidence across all rules")
    rules: list[RuleCheckResult] = Field(default_factory=list)
    critical_failures: int = Field(0, description="Rules that failed with confidence > 0.8")


# ---------------------------------------------------------------------------
# Prompt templates for the advisor
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """\
You are a PDF accessibility pipeline diagnostics expert.
A processing step has failed. Classify the failure and generate targeted questions.

Failure details (JSON):
{error_json}

Respond with strict raw JSON (no markdown fences) matching this structure:
{{
  "category": "zone" | "tag" | "ingestion",
  "severity": "warning" | "error" | "critical",
  "summary": "<one sentence>",
  "confidence": <0.0-1.0>,
  "questions": [
    {{
      "question_id": "<slug>",
      "question": "<question text>",
      "category": "zone" | "tag" | "ingestion",
      "choices": ["<opt1>", "<opt2>"]  // or [] for free-text
    }}
  ]
}}

Rules:
- Generate 1–3 questions only. Each question must be specific to the failure.
- For ZONE errors: ask about column count, reading-order anomalies, or sidebar presence.
- For TAG errors: ask about heading level jumps, orphan titles, or artifact misclassification.
- For INGESTION errors: ask about PDF encryption, scanned image quality, or font encoding.
- severity is "critical" only if the fallback also failed (is_fallback=true AND exception present).
"""

_RESOLVE_PROMPT = """\
You are a PDF accessibility pipeline repair expert.
Given a classified failure and the user's answers to clarifying questions,
produce a concrete resolution plan.

Failure + classification (JSON):
{diagnosis_json}

User answers (JSON):
{answers_json}

Respond with strict raw JSON (no markdown fences):
{{
  "resolution": [
    {{
      "action": "<what to do>",
      "target": "<which file / parameter / prompt>",
      "rationale": "<why this fixes it>",
      "auto_applicable": true | false
    }}
  ]
}}

Rules:
- Provide 1–4 resolution steps, ordered by impact.
- Mark auto_applicable=true only for changes the system can make without user input
  (e.g. adjusting a prompt parameter, increasing DPI, enabling fallback mode).
- For critical failures, always include a manual fallback step.
"""

_ACCURACY_PROMPT = """\
You are a certified PDF/UA and WCAG 2.2 compliance auditor.
Review the following TagTree JSON for one page and check each rule below.

TagTree (JSON):
{tag_tree_json}

Check these rules and respond with strict raw JSON (no markdown fences):
{{
  "overall_score": <0.0-1.0>,
  "rules": [
    {{
      "rule_id": "<id>",
      "rule_description": "<description>",
      "passed": true | false,
      "confidence": <0.0-1.0>,
      "issue": "<violation description or null>",
      "suggested_fix": "<fix or null>"
    }}
  ]
}}

Rules to check:
1. PDF-UA-1/7.1 — All real content is tagged; no untagged text spans exist.
2. PDF-UA-1/7.2 — Heading levels are not skipped (no H1→H3 without H2).
3. PDF-UA-1/7.5 — All Figure nodes have non-empty Alt attribute.
4. PDF-UA-1/7.6 — Table TH cells have Scope or ID attributes.
5. WCAG-2.2/1.3.1 — Information conveyed by structure is also in the tag tree.
6. WCAG-2.2/1.3.2 — Reading order of tags matches visual reading order.
7. WCAG-2.2/1.1.1 — All non-text content has a text alternative.
"""


# ---------------------------------------------------------------------------
# TroubleshootingAdvisor
# ---------------------------------------------------------------------------

class TroubleshootingAdvisor:
    """
    LLM-backed diagnostic advisor.

    Usage (two-phase):
    ------------------
    advisor = TroubleshootingAdvisor(client, api_url, api_key, model)

    # Phase 1 — classify and get questions:
    diagnosis = await advisor.diagnose(error)

    # Phase 2 — user answers questions, get resolution:
    answers = {"q_zone_column_count": "2 columns", "q_zone_sidebar": "yes"}
    resolved = await advisor.resolve(diagnosis, answers)
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_url: str,
        api_key: str,
        model: str,
    ) -> None:
        self._client = client
        self._api_url = api_url
        self._api_key = api_key
        self._model = model

    async def diagnose(self, error: PipelineError) -> TroubleshootingDiagnosis:
        """
        Phase 1: Classify the error and generate clarifying questions.
        Returns a TroubleshootingDiagnosis with questions populated,
        resolution left empty (filled by resolve()).
        """
        prompt = _CLASSIFY_PROMPT.format(error_json=error.model_dump_json(indent=2))
        try:
            raw = await _call_llm_with_retry(
                self._client, self._api_url, self._api_key, self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            data = _parse_json_strict(raw, "troubleshooter/diagnose")
            questions = [ClarifyingQuestion(**q) for q in data.get("questions", [])]
            return TroubleshootingDiagnosis(
                error=error,
                category=ErrorCategory(data["category"]),
                severity=data.get("severity", "warning"),
                summary=data.get("summary", "An error occurred in the pipeline."),
                questions=questions,
                confidence=float(data.get("confidence", 0.5)),
            )
        except Exception as exc:
            logger.error("TroubleshootingAdvisor.diagnose failed: %s", exc)
            # Return a minimal safe diagnosis so the UI can still show something.
            return TroubleshootingDiagnosis(
                error=error,
                category=_classify_heuristic(error),
                severity="warning" if not error.is_fallback else "error",
                summary=f"Pipeline step '{error.step_name}' failed: {error.exception_message}",
                questions=[],
                confidence=0.0,
            )

    async def resolve(
        self,
        diagnosis: TroubleshootingDiagnosis,
        answers: dict[str, str],
    ) -> TroubleshootingDiagnosis:
        """
        Phase 2: Given user answers, produce a concrete resolution plan.
        Returns the same diagnosis with resolution steps populated.
        """
        prompt = _RESOLVE_PROMPT.format(
            diagnosis_json=diagnosis.model_dump_json(indent=2),
            answers_json=str(answers),
        )
        try:
            raw = await _call_llm_with_retry(
                self._client, self._api_url, self._api_key, self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            data = _parse_json_strict(raw, "troubleshooter/resolve")
            steps = [ResolutionStep(**s) for s in data.get("resolution", [])]
            diagnosis.resolution = steps
        except Exception as exc:
            logger.error("TroubleshootingAdvisor.resolve failed: %s", exc)
            diagnosis.resolution = [
                ResolutionStep(
                    action="Retry the failed step with increased timeout and DPI.",
                    target="ai_pipeline.py / pdf_processor.render_pdf_to_images()",
                    rationale="Most transient failures are resolved by retry with better input quality.",
                    auto_applicable=True,
                )
            ]
        return diagnosis


# ---------------------------------------------------------------------------
# Periodic accuracy check
# ---------------------------------------------------------------------------

async def run_accuracy_check(
    tag_tree: TagTree,
    client: httpx.AsyncClient,
    api_url: str,
    api_key: str,
    model: str,
) -> AccuracyReport:
    """
    Ask the LLM to audit a single page's TagTree against 7 PDF/UA + WCAG rules.
    Returns an AccuracyReport with per-rule pass/fail and confidence scores.

    Intended to be called after every page (or every N pages for large docs).
    """
    prompt = _ACCURACY_PROMPT.format(tag_tree_json=tag_tree.model_dump_json(indent=2))
    try:
        raw = await _call_llm_with_retry(
            client, api_url, api_key, model,
            messages=[{"role": "user", "content": prompt}],
        )
        data = _parse_json_strict(raw, "accuracy_check")
        rules = [RuleCheckResult(**r) for r in data.get("rules", [])]
        overall = float(data.get("overall_score", 0.0))
        critical = sum(1 for r in rules if not r.passed and r.confidence > 0.8)
        return AccuracyReport(
            page_number=tag_tree.page_number,
            overall_score=overall,
            rules=rules,
            critical_failures=critical,
        )
    except Exception as exc:
        logger.error("run_accuracy_check failed for page %d: %s", tag_tree.page_number, exc)
        return AccuracyReport(page_number=tag_tree.page_number, overall_score=0.0)


# ---------------------------------------------------------------------------
# Heuristic fallback classifier (used when LLM advisor itself fails)
# ---------------------------------------------------------------------------

def _classify_heuristic(error: PipelineError) -> ErrorCategory:
    """Classify an error without LLM help, based on step index and message."""
    msg = error.exception_message.lower()
    if error.step == 0 or "ocr" in msg or "image" in msg or "decode" in msg:
        return ErrorCategory.INGESTION
    if error.step in (1, 2) or "zone" in msg or "layout" in msg or "column" in msg:
        return ErrorCategory.ZONE
    return ErrorCategory.TAG
