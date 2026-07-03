"""
tests/test_api.py — End-to-end API tests for the PDF remediation backend.

Uses FastAPI's built-in TestClient (HTTPX-backed, no live server needed).
Tests cover every endpoint against a real minimal PDF generated in-memory
by PyMuPDF — so no fixture files are needed and tests run offline.

Endpoints tested
----------------
  POST /check          → accessibility report on a plain PDF
  POST /jobs           → job creation + page count + ETA estimate
  GET  /jobs/{id}/status → job state snapshot
  GET  /jobs/{id}/stream → SSE events (job_start at minimum, non-blocking)
  GET  /jobs/{id}/download → 404 while pending, 202 while running
  DELETE /jobs/{id}    → 204 cleanup

The pipeline steps (LLM calls) are NOT invoked — the LLM_API_KEY is empty
so the pipeline will error gracefully and fall back. These tests validate
the API contract, response shapes, and status codes — not LLM output quality.
"""

import io
import time
import pytest
import fitz
from fastapi.testclient import TestClient

# Import the FastAPI app
from app import app

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_minimal_pdf(
    pages: int = 2,
    title: str = "Test Document",
    tagged: bool = False,
) -> bytes:
    """
    Generate a minimal in-memory PDF with real text content using PyMuPDF.
    Optionally mark as tagged so /check tests can cover both states.
    """
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_text(
            (72, 100 + i * 20),
            f"Page {i + 1} — Heading One",
            fontsize=18,
        )
        page.insert_text(
            (72, 140 + i * 20),
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
            fontsize=11,
        )
    if tagged:
        doc.set_markinfo({"Marked": True})
        doc.set_language("en")
        doc.set_metadata({"title": title})
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def pdf_upload(pdf_bytes: bytes, filename: str = "test.pdf"):
    """Return a files dict suitable for TestClient multipart upload."""
    return {"file": (filename, pdf_bytes, "application/pdf")}


# ---------------------------------------------------------------------------
# POST /check — accessibility checker
# ---------------------------------------------------------------------------

class TestCheckEndpoint:

    def test_check_untagged_pdf_returns_report(self):
        pdf = make_minimal_pdf(pages=1, tagged=False)
        r = client.post("/check", files=pdf_upload(pdf))
        assert r.status_code == 200
        data = r.json()
        assert data["filename"] == "test.pdf"
        assert data["page_count"] == 1
        assert data["is_tagged"] is False
        assert isinstance(data["overall_score"], int)
        assert data["overall_score"] >= 0
        assert data["grade"] in ("A", "B", "C", "D", "F")
        assert isinstance(data["checks"], list)
        assert len(data["checks"]) == 10  # all 10 rules always run
        assert isinstance(data["summary"], str)

    def test_check_tagged_pdf_scores_higher(self):
        untagged = make_minimal_pdf(pages=1, tagged=False)
        tagged   = make_minimal_pdf(pages=1, tagged=True)
        r_un = client.post("/check", files=pdf_upload(untagged)).json()
        r_ta = client.post("/check", files=pdf_upload(tagged)).json()
        assert r_ta["overall_score"] > r_un["overall_score"], (
            "Tagged PDF should score higher than untagged"
        )

    def test_check_report_has_correct_rule_ids(self):
        pdf = make_minimal_pdf()
        data = client.post("/check", files=pdf_upload(pdf)).json()
        rule_ids = {c["rule_id"] for c in data["checks"]}
        expected = {"STRUCT", "TITLE", "LANG", "HEADINGS", "ALT",
                    "TABLES", "READING", "ARTIF", "UNICODE", "ROLE_MAP"}
        assert rule_ids == expected

    def test_check_each_result_has_status(self):
        pdf = make_minimal_pdf()
        data = client.post("/check", files=pdf_upload(pdf)).json()
        for check in data["checks"]:
            assert check["status"] in ("pass", "warn", "fail")
            assert isinstance(check["message"], str)
            assert len(check["message"]) > 0

    def test_check_rejects_non_pdf(self):
        r = client.post("/check", files={"file": ("doc.txt", b"hello", "text/plain")})
        assert r.status_code == 400

    def test_check_multipage_pdf(self):
        pdf = make_minimal_pdf(pages=5)
        data = client.post("/check", files=pdf_upload(pdf)).json()
        assert data["page_count"] == 5

    def test_check_tagged_pdf_struct_passes(self):
        pdf = make_minimal_pdf(tagged=True)
        data = client.post("/check", files=pdf_upload(pdf)).json()
        struct = next(c for c in data["checks"] if c["rule_id"] == "STRUCT")
        assert struct["status"] == "pass"

    def test_check_untagged_pdf_struct_fails(self):
        pdf = make_minimal_pdf(tagged=False)
        data = client.post("/check", files=pdf_upload(pdf)).json()
        struct = next(c for c in data["checks"] if c["rule_id"] == "STRUCT")
        assert struct["status"] == "fail"


# ---------------------------------------------------------------------------
# POST /jobs — job creation
# ---------------------------------------------------------------------------

class TestCreateJob:

    def test_create_job_returns_202(self):
        pdf = make_minimal_pdf(pages=3)
        r = client.post("/jobs", files=pdf_upload(pdf))
        assert r.status_code == 202

    def test_create_job_response_shape(self):
        pdf = make_minimal_pdf(pages=2)
        data = client.post("/jobs", files=pdf_upload(pdf)).json()
        assert "job_id" in data
        assert "total_pages" in data
        assert "estimated_seconds" in data
        assert "message" in data

    def test_create_job_page_count_matches(self):
        for n in (1, 3, 5):
            pdf = make_minimal_pdf(pages=n)
            data = client.post("/jobs", files=pdf_upload(pdf)).json()
            assert data["total_pages"] == n, f"Expected {n} pages, got {data['total_pages']}"

    def test_create_job_estimated_seconds_positive(self):
        pdf = make_minimal_pdf(pages=2)
        data = client.post("/jobs", files=pdf_upload(pdf)).json()
        assert data["estimated_seconds"] > 0

    def test_create_job_rejects_non_pdf(self):
        r = client.post("/jobs", files={"file": ("x.docx", b"data", "application/octet-stream")})
        assert r.status_code == 400

    def test_create_job_returns_unique_ids(self):
        pdf = make_minimal_pdf(pages=1)
        ids = [
            client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]
            for _ in range(3)
        ]
        assert len(set(ids)) == 3, "Each job must receive a unique ID"


# ---------------------------------------------------------------------------
# GET /jobs/{id}/status — status snapshot
# ---------------------------------------------------------------------------

class TestJobStatus:

    @pytest.fixture
    def job_id(self):
        pdf = make_minimal_pdf(pages=1)
        return client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]

    def test_status_returns_200(self, job_id):
        r = client.get(f"/jobs/{job_id}/status")
        assert r.status_code == 200

    def test_status_shape(self, job_id):
        data = client.get(f"/jobs/{job_id}/status").json()
        for field in ("job_id", "status", "pages_done", "total_pages",
                      "eta_remaining_s", "warning_count", "error_count"):
            assert field in data, f"Missing field: {field}"

    def test_status_initial_state(self, job_id):
        data = client.get(f"/jobs/{job_id}/status").json()
        assert data["job_id"] == job_id
        assert data["status"] in ("pending", "running", "done", "error")
        assert data["pages_done"] >= 0
        assert data["total_pages"] >= 1

    def test_status_404_unknown_job(self):
        r = client.get("/jobs/does-not-exist/status")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /jobs/{id}/download — download gating
# ---------------------------------------------------------------------------

class TestDownload:

    @pytest.fixture
    def job_id(self):
        pdf = make_minimal_pdf(pages=1)
        return client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]

    def test_download_before_done_not_200(self, job_id):
        r = client.get(f"/jobs/{job_id}/download")
        # Should be 202 (still running) or 500 (failed) — never 200 immediately
        assert r.status_code in (202, 500)

    def test_download_unknown_job_is_404(self):
        r = client.get("/jobs/no-such-job/download")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /jobs/{id}/stream — SSE events
# ---------------------------------------------------------------------------

class TestSSEStream:

    def test_stream_content_type(self):
        pdf = make_minimal_pdf(pages=1)
        job_id = client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]
        # Open SSE stream but read only the first chunk — don't block
        with client.stream("GET", f"/jobs/{job_id}/stream") as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers.get("content-type", "")

    def test_stream_emits_job_start(self):
        import json
        pdf = make_minimal_pdf(pages=1)
        job_id = client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]
        events = []
        with client.stream("GET", f"/jobs/{job_id}/stream") as r:
            for line in r.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
                if any(e.get("type") == "job_start" for e in events):
                    break  # got what we need — stop reading
        types = [e["type"] for e in events]
        assert "job_start" in types

    def test_stream_job_start_has_page_count(self):
        import json
        pages = 2
        pdf = make_minimal_pdf(pages=pages)
        job_id = client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]
        with client.stream("GET", f"/jobs/{job_id}/stream") as r:
            for line in r.iter_lines():
                if line.startswith("data:"):
                    ev = json.loads(line[5:].strip())
                    if ev.get("type") == "job_start":
                        assert ev["total_pages"] == pages
                        break


# ---------------------------------------------------------------------------
# DELETE /jobs/{id} — cleanup
# ---------------------------------------------------------------------------

class TestDeleteJob:

    def test_delete_returns_204(self):
        pdf = make_minimal_pdf(pages=1)
        job_id = client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]
        r = client.delete(f"/jobs/{job_id}")
        assert r.status_code == 204

    def test_delete_then_status_is_404(self):
        pdf = make_minimal_pdf(pages=1)
        job_id = client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]
        client.delete(f"/jobs/{job_id}")
        r = client.get(f"/jobs/{job_id}/status")
        assert r.status_code == 404

    def test_delete_unknown_job_is_404(self):
        r = client.delete("/jobs/ghost-job")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /jobs/{id}/troubleshoot — requires an error to exist in the job
# ---------------------------------------------------------------------------

class TestTroubleshoot:

    def test_troubleshoot_invalid_error_index(self):
        pdf = make_minimal_pdf(pages=1)
        job_id = client.post("/jobs", files=pdf_upload(pdf)).json()["job_id"]
        r = client.post(
            f"/jobs/{job_id}/troubleshoot",
            json={"error_index": 999},
        )
        assert r.status_code == 400

    def test_troubleshoot_unknown_job(self):
        r = client.post(
            "/jobs/no-such-job/troubleshoot",
            json={"error_index": 0},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Schemas smoke-test — Pydantic models instantiate correctly
# ---------------------------------------------------------------------------

class TestSchemas:

    def test_bounding_box_valid(self):
        from schemas import BoundingBox
        bb = BoundingBox(xmin=0, ymin=0, xmax=500, ymax=600)
        assert bb.xmax == 500

    def test_bounding_box_invalid_orientation(self):
        from schemas import BoundingBox
        with pytest.raises(Exception):
            BoundingBox(xmin=500, ymin=0, xmax=100, ymax=600)

    def test_global_context_heading_window(self):
        from schemas import GlobalContext
        ctx = GlobalContext()
        for depth in range(1, 20):
            ctx.record_heading(depth % 6 + 1)
        assert len(ctx.heading_history) <= 12, "heading_history must cap at 12"

    def test_tag_tree_builds(self):
        from schemas import TagTree, TagNode, PdfTag
        tree = TagTree(
            page_number=1,
            nodes=[
                TagNode(tag=PdfTag.H1, content="Title"),
                TagNode(tag=PdfTag.P, content="Body text."),
            ],
        )
        assert len(tree.nodes) == 2

    def test_ocr_page_tokens(self):
        from schemas import OcrPage, OcrToken, BoundingBox
        page = OcrPage(
            page_number=1,
            tokens=[
                OcrToken(
                    text="Hello",
                    bounding_box=BoundingBox(xmin=10, ymin=10, xmax=100, ymax=30),
                )
            ],
        )
        assert page.tokens[0].text == "Hello"


# ---------------------------------------------------------------------------
# checker.py unit tests — deterministic, no server needed
# ---------------------------------------------------------------------------

class TestChecker:

    def test_check_pdf_returns_report(self):
        from checker import check_pdf
        pdf = make_minimal_pdf(pages=2)
        report = check_pdf(pdf, filename="myfile.pdf")
        assert report.filename == "myfile.pdf"
        assert report.page_count == 2
        assert 0 <= report.overall_score <= 100
        assert report.grade in ("A", "B", "C", "D", "F")

    def test_check_pdf_10_checks(self):
        from checker import check_pdf
        report = check_pdf(make_minimal_pdf())
        assert len(report.checks) == 10

    def test_check_pdf_tagged_higher_score(self):
        from checker import check_pdf
        score_plain  = check_pdf(make_minimal_pdf(tagged=False)).overall_score
        score_tagged = check_pdf(make_minimal_pdf(tagged=True)).overall_score
        assert score_tagged > score_plain

    def test_check_pdf_grade_F_untagged(self):
        from checker import check_pdf
        report = check_pdf(make_minimal_pdf(tagged=False))
        # An untagged doc fails STRUCT (weight 20) + LANG (10) + READING (10)
        # = at most 60/100, landing in C or below
        assert report.grade in ("C", "D", "F")

    def test_check_pdf_invalid_bytes_raises(self):
        from checker import check_pdf
        with pytest.raises(ValueError):
            check_pdf(b"this is not a pdf", filename="bad.pdf")
