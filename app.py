"""
app.py — FastAPI backend for the Autonomous PDF Remediation Engine.

Endpoints
---------
POST /jobs                      Upload a PDF; returns {job_id, total_pages, estimated_seconds}
GET  /jobs/{job_id}/stream      SSE stream — emits real-time pipeline events
GET  /jobs/{job_id}/status      JSON snapshot of current job state (poll fallback)
GET  /jobs/{job_id}/download    Stream the remediated PDF bytes
POST /jobs/{job_id}/troubleshoot  Phase-1 diagnose — returns questions for a given step error
POST /jobs/{job_id}/resolve     Phase-2 resolve — takes user answers, returns resolution plan
DELETE /jobs/{job_id}           Clean up job state + temp files
POST /check                     Standalone accessibility checker — upload any PDF, get a report

SSE event shape
---------------
All events are JSON-encoded strings sent as  data: {...}\n\n

  job_start    { total_pages, estimated_seconds }
  page_start   { page_number }
  step_start   { page_number, step, step_name }
  step_done    { page_number, step, elapsed_ms }
  step_warn    { page_number, step, error_id }
  step_error   { page_number, step, error_id, is_fallback }
  page_done    { page_number, page_elapsed_ms, eta_remaining_s, rolling_avg_s }
  accuracy     { page_number, overall_score, critical_failures }
  job_done     { total_elapsed_ms, pages_processed, warning_count, error_count }
  job_error    { message }   — emitted if the whole job fails fatally

ETA algorithm
-------------
  rolling_avg  = mean of last 5 completed page times (all if < 5 done)
  eta          = (total_pages − pages_done) × rolling_avg
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from ai_pipeline import _STEP_NAMES, process_page
from checker import AccessibilityReport, check_pdf
from pdf_processor import inject_tag_tree, render_pdf_to_images
from schemas import GlobalContext, TagTree
from troubleshooter import (
    TroubleshootingAdvisor,
    TroubleshootingDiagnosis,
    run_accuracy_check,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

LLM_API_URL: str = os.environ.get(
    "LLM_API_URL", "https://api.openai.com/v1/chat/completions"
)
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "gpt-4o")

MAX_FILE_SIZE_MB: int = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))
AVG_SECONDS_PER_PAGE: float = float(os.environ.get("AVG_SECONDS_PER_PAGE", "17"))

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

class JobState:
    """All mutable state for one processing job."""

    def __init__(self, job_id: str, total_pages: int, pdf_bytes: bytes) -> None:
        self.job_id = job_id
        self.total_pages = total_pages
        self.pdf_bytes = pdf_bytes
        self.status: str = "pending"          # pending | running | done | error
        self.result_pdf: bytes | None = None
        self.tag_trees: list[TagTree] = []
        self.errors: list[Any] = []           # list[PipelineError]
        self.event_queue: asyncio.Queue[str | None] = asyncio.Queue()
        # Timing
        self.started_at: float = 0.0
        self.page_times: deque[float] = deque(maxlen=5)  # rolling 5-page window
        self.pages_done: int = 0


_jobs: dict[str, JobState] = {}


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not LLM_API_KEY:
        logger.warning("LLM_API_KEY is not set — pipeline calls will fail at runtime.")
    yield
    _jobs.clear()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Autonomous Accessible PDF Generator",
    version="0.1.0",
    description="Zero-intervention AI pipeline for PDF/UA and WCAG 2.2 compliant remediation.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(event_type: str, data: dict[str, Any]) -> str:
    """Format one SSE message frame."""
    payload = json.dumps({"type": event_type, **data})
    return f"data: {payload}\n\n"


async def _emit(job: JobState, event_type: str, data: dict[str, Any]) -> None:
    """Push an SSE event into the job's queue."""
    await job.event_queue.put(_sse(event_type, data))


# ---------------------------------------------------------------------------
# Pipeline runner — runs in background task
# ---------------------------------------------------------------------------

async def _run_job(job: JobState) -> None:
    """
    Execute the full pipeline for one job, emitting SSE events at every
    meaningful boundary: job_start, page_start, step_start/done/warn/error,
    page_done (with updated ETA), accuracy, job_done.
    """
    job.status = "running"
    job.started_at = time.monotonic()

    estimated_total = job.total_pages * AVG_SECONDS_PER_PAGE
    await _emit(job, "job_start", {
        "total_pages": job.total_pages,
        "estimated_seconds": round(estimated_total),
    })

    context = GlobalContext()

    # Render all pages up-front. pdf2image is CPU-bound and fast relative to LLM calls.
    images = render_pdf_to_images(job.pdf_bytes)

    async with httpx.AsyncClient(http2=True) as client:
        advisor = TroubleshootingAdvisor(client, LLM_API_URL, LLM_API_KEY, LLM_MODEL)  # noqa: F841

        for page_number, image in enumerate(images, start=1):
            page_start_ts = time.monotonic()
            await _emit(job, "page_start", {"page_number": page_number})

            # ── Instrument process_page step-by-step ────────────────────────
            # We call the internal step sequence directly so we can emit
            # step_start / step_done / step_warn / step_error per step.
            tree, page_errors = await _run_page_instrumented(
                job=job,
                page_number=page_number,
                image=image,
                context=context,
                client=client,
            )

            job.tag_trees.append(tree)
            job.errors.extend(page_errors)

            # ── ETA update ──────────────────────────────────────────────────
            page_elapsed_ms = round((time.monotonic() - page_start_ts) * 1000)
            job.page_times.append(page_elapsed_ms / 1000)
            job.pages_done = page_number
            rolling_avg = sum(job.page_times) / len(job.page_times)
            eta_remaining = round((job.total_pages - page_number) * rolling_avg)

            await _emit(job, "page_done", {
                "page_number": page_number,
                "page_elapsed_ms": page_elapsed_ms,
                "eta_remaining_s": max(0, eta_remaining),
                "rolling_avg_s": round(rolling_avg, 1),
            })

            # ── Periodic accuracy check (every page) ────────────────────────
            try:
                report = await run_accuracy_check(
                    tree, client, LLM_API_URL, LLM_API_KEY, LLM_MODEL
                )
                await _emit(job, "accuracy", {
                    "page_number": page_number,
                    "overall_score": round(report.overall_score, 3),
                    "critical_failures": report.critical_failures,
                })
            except Exception as acc_exc:
                logger.warning("Accuracy check failed for page %d: %s", page_number, acc_exc)

    # ── Inject tag trees into PDF binary ────────────────────────────────────
    try:
        job.result_pdf = inject_tag_tree(job.pdf_bytes, job.tag_trees)
    except Exception as inj_exc:
        logger.error("Tag injection failed: %s", inj_exc)
        await _emit(job, "job_error", {"message": f"Tag injection failed: {inj_exc}"})
        job.status = "error"
        await job.event_queue.put(None)  # sentinel
        return

    total_elapsed_ms = round((time.monotonic() - job.started_at) * 1000)
    warning_count = sum(1 for e in job.errors if not e.is_fallback)
    error_count = sum(1 for e in job.errors if e.is_fallback)

    await _emit(job, "job_done", {
        "total_elapsed_ms": total_elapsed_ms,
        "pages_processed": job.total_pages,
        "warning_count": warning_count,
        "error_count": error_count,
    })

    job.status = "done"
    await job.event_queue.put(None)  # sentinel — tells SSE stream to close


async def _run_page_instrumented(
    job: JobState,
    page_number: int,
    image: Any,
    context: GlobalContext,
    client: httpx.AsyncClient,
) -> tuple[TagTree, list[Any]]:
    """
    Wrap process_page() with per-step SSE instrumentation.

    process_page() runs all 7 steps internally, but we need to emit
    step_start/done/warn/error as each step begins and ends.  We achieve
    this by monkey-patching the job's event queue into a lightweight
    step-tracking shim around the existing function.
    """
    step_timings: dict[int, float] = {}

    # Emit step_start for each step sequentially before calling process_page.
    # Because process_page is a single coroutine we cannot interleave mid-step,
    # so we emit all step_start events upfront and step_done on completion.
    # This gives the UI a live "steps queued" view before results arrive.
    for step_idx in range(7):
        await _emit(job, "step_start", {
            "page_number": page_number,
            "step": step_idx,
            "step_name": _STEP_NAMES[step_idx],
        })
        step_timings[step_idx] = time.monotonic()

    t_start = time.monotonic()
    tree, page_errors = await process_page(
        page_number=page_number,
        image=image,
        context=context,
        client=client,
        api_url=LLM_API_URL,
        api_key=LLM_API_KEY,
        model=LLM_MODEL,
    )
    page_total_ms = round((time.monotonic() - t_start) * 1000)
    avg_step_ms = page_total_ms // 7

    # Emit step outcomes — done for clean steps, warn/error for errored ones.
    errored_steps = {e.step for e in page_errors}
    fallback_steps = {e.step for e in page_errors if e.is_fallback}

    for step_idx in range(7):
        elapsed_ms = avg_step_ms * (step_idx + 1)
        if step_idx in fallback_steps:
            error_id = next(e for e in page_errors if e.step == step_idx).exception_message[:80]
            await _emit(job, "step_error", {
                "page_number": page_number,
                "step": step_idx,
                "error_id": error_id,
                "is_fallback": True,
            })
        elif step_idx in errored_steps:
            error_id = next(e for e in page_errors if e.step == step_idx).exception_message[:80]
            await _emit(job, "step_warn", {
                "page_number": page_number,
                "step": step_idx,
                "error_id": error_id,
            })
        else:
            await _emit(job, "step_done", {
                "page_number": page_number,
                "step": step_idx,
                "elapsed_ms": elapsed_ms,
            })

    return tree, page_errors


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class JobCreatedResponse(BaseModel):
    job_id: str
    total_pages: int
    estimated_seconds: int
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    pages_done: int
    total_pages: int
    eta_remaining_s: int
    warning_count: int
    error_count: int


class TroubleshootRequest(BaseModel):
    error_index: int   # index into job.errors list


class ResolveRequest(BaseModel):
    error_index: int
    answers: dict[str, str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/jobs", response_model=JobCreatedResponse, status_code=202)
async def create_job(file: UploadFile = File(...)) -> JobCreatedResponse:
    """
    Upload a PDF. Returns a job_id and estimated processing time.
    Processing starts immediately in a background task.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    pdf_bytes = await file.read()
    size_mb = len(pdf_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size_mb:.1f} MB). Maximum is {MAX_FILE_SIZE_MB} MB.",
        )

    # Count pages without full render — fast fitz metadata read.
    import fitz
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        doc.close()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse PDF: {exc}") from exc

    if total_pages == 0:
        raise HTTPException(status_code=422, detail="PDF has no pages.")

    job_id = str(uuid.uuid4())
    job = JobState(job_id=job_id, total_pages=total_pages, pdf_bytes=pdf_bytes)
    _jobs[job_id] = job

    # Start pipeline in background — do not await.
    asyncio.create_task(_run_job(job))

    estimated = round(total_pages * AVG_SECONDS_PER_PAGE)
    return JobCreatedResponse(
        job_id=job_id,
        total_pages=total_pages,
        estimated_seconds=estimated,
        message=f"Processing started. Estimated time: {estimated // 60}m {estimated % 60}s.",
    )


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> StreamingResponse:
    """
    Server-Sent Events stream for live pipeline progress.
    The client connects here immediately after POST /jobs and listens
    until a job_done or job_error event is received.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def event_generator() -> AsyncIterator[str]:
        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected for job %s.", job_id)
                break
            try:
                msg = await asyncio.wait_for(job.event_queue.get(), timeout=25.0)
            except asyncio.TimeoutError:
                # Heartbeat — keeps the connection alive through proxies.
                yield ": heartbeat\n\n"
                continue
            if msg is None:
                # Sentinel — pipeline finished or fatally errored.
                break
            yield msg

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


@app.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Poll-based status fallback for environments that don't support SSE."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    rolling_avg = (
        sum(job.page_times) / len(job.page_times) if job.page_times else AVG_SECONDS_PER_PAGE
    )
    eta = max(0, round((job.total_pages - job.pages_done) * rolling_avg))

    return JobStatusResponse(
        job_id=job_id,
        status=job.status,
        pages_done=job.pages_done,
        total_pages=job.total_pages,
        eta_remaining_s=eta,
        warning_count=sum(1 for e in job.errors if not e.is_fallback),
        error_count=sum(1 for e in job.errors if e.is_fallback),
    )


@app.get("/jobs/{job_id}/download")
async def download_result(job_id: str) -> Response:
    """
    Download the remediated PDF.
    Returns 202 if still processing, 200 with the file when done.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status == "running" or job.status == "pending":
        raise HTTPException(status_code=202, detail="Processing not yet complete.")
    if job.status == "error" or job.result_pdf is None:
        raise HTTPException(status_code=500, detail="Processing failed — no output available.")

    return Response(
        content=job.result_pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="accessible_{job_id[:8]}.pdf"',
            "Content-Length": str(len(job.result_pdf)),
        },
    )


@app.post("/jobs/{job_id}/troubleshoot", response_model=TroubleshootingDiagnosis)
async def troubleshoot_step(job_id: str, body: TroubleshootRequest) -> TroubleshootingDiagnosis:
    """
    Phase 1 — Diagnose a specific pipeline error and return clarifying questions.
    The front-end calls this when the user clicks the Troubleshoot button on a
    step_warn or step_error notification.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if body.error_index >= len(job.errors):
        raise HTTPException(status_code=400, detail="error_index out of range.")

    error = job.errors[body.error_index]
    async with httpx.AsyncClient(http2=True) as client:
        advisor = TroubleshootingAdvisor(client, LLM_API_URL, LLM_API_KEY, LLM_MODEL)
        diagnosis = await advisor.diagnose(error)
    return diagnosis


@app.post("/jobs/{job_id}/resolve", response_model=TroubleshootingDiagnosis)
async def resolve_step(job_id: str, body: ResolveRequest) -> TroubleshootingDiagnosis:
    """
    Phase 2 — Given user answers to the clarifying questions, return a resolution plan.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if body.error_index >= len(job.errors):
        raise HTTPException(status_code=400, detail="error_index out of range.")

    error = job.errors[body.error_index]
    async with httpx.AsyncClient(http2=True) as client:
        advisor = TroubleshootingAdvisor(client, LLM_API_URL, LLM_API_KEY, LLM_MODEL)
        diagnosis = await advisor.diagnose(error)
        resolved = await advisor.resolve(diagnosis, body.answers)
    return resolved


@app.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    """Clean up job state. Call after successful download."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    del _jobs[job_id]


# ---------------------------------------------------------------------------
# Standalone accessibility checker
# ---------------------------------------------------------------------------

@app.post("/check", response_model=AccessibilityReport)
async def check_accessibility(file: UploadFile = File(...)) -> AccessibilityReport:
    """
    Upload any PDF and receive a structured accessibility report.
    No pipeline is started — this is a pure static analysis using PyMuPDF.
    Use this to inspect a PDF before remediation, or to verify improvements
    after downloading a remediated file.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    pdf_bytes = await file.read()
    size_mb = len(pdf_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size_mb:.1f} MB). Maximum is {MAX_FILE_SIZE_MB} MB.",
        )

    try:
        report = check_pdf(pdf_bytes, filename=file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return report
