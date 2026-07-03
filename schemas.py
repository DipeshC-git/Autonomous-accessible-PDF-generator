"""
schemas.py — Pydantic data contracts for the Autonomous PDF Remediation Engine.

Each model maps to exactly one inter-step boundary in the 7-prompt pipeline:

  PROMPT_0  →  OcrToken / OcrPage
  PROMPT_1  →  LayoutZone / LayoutBlueprint
  PROMPT_2  →  ZoneText / ZoneTextMap
  PROMPT_3  →  TagNode / TagTree          (initial pass, headings + text)
  PROMPT_4  →  ImageTag                   (appended into TagTree)
  PROMPT_5  →  TableCell / TableMatrix    (merged into TagTree)
  PROMPT_6  →  TagTree                    (final validated output)

GlobalContext threads through PROMPT_3 and PROMPT_5 to carry cross-page state.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class BoundingBox(BaseModel):
    """Normalised coordinates scaled 0–1000 (origin: top-left)."""
    xmin: float = Field(..., ge=0, le=1000)
    ymin: float = Field(..., ge=0, le=1000)
    xmax: float = Field(..., ge=0, le=1000)
    ymax: float = Field(..., ge=0, le=1000)

    @model_validator(mode="after")
    def _check_orientation(self) -> "BoundingBox":
        if self.xmax <= self.xmin or self.ymax <= self.ymin:
            raise ValueError(
                f"Invalid bounding box: max must be greater than min "
                f"(got xmin={self.xmin}, xmax={self.xmax}, "
                f"ymin={self.ymin}, ymax={self.ymax})"
            )
        return self


# ---------------------------------------------------------------------------
# PROMPT_0 output — OCR tokens
# ---------------------------------------------------------------------------

class OcrToken(BaseModel):
    """A single extracted text fragment with its page position."""
    text: str = Field(..., min_length=1)
    bounding_box: BoundingBox


class OcrPage(BaseModel):
    """All tokens extracted from one page (PROMPT_0 output)."""
    page_number: int = Field(..., ge=1)
    tokens: list[OcrToken] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# PROMPT_1 output — layout blueprint
# ---------------------------------------------------------------------------

class ZoneType(str, Enum):
    HEADER     = "Header"
    FOOTER     = "Footer"
    PARAGRAPH  = "Paragraph"
    HEADING_1  = "Heading1"
    HEADING_2  = "Heading2"
    HEADING_3  = "Heading3"
    HEADING_4  = "Heading4"
    HEADING_5  = "Heading5"
    HEADING_6  = "Heading6"
    LIST       = "List"
    TABLE      = "Table"
    IMAGE      = "Image"
    SIDEBAR    = "Sidebar"
    DECORATIVE = "Decorative"


class LayoutZone(BaseModel):
    """One identified layout region on a page."""
    zone_id: str = Field(..., description="Unique identifier within the page, e.g. 'p3_z2'")
    zone_type: ZoneType
    reading_order: int = Field(..., ge=0, description="0-based sequential reading position")
    bounding_box: BoundingBox


class LayoutBlueprint(BaseModel):
    """Complete layout map for one page (PROMPT_1 output)."""
    page_number: int = Field(..., ge=1)
    zones: list[LayoutZone] = Field(default_factory=list)

    @field_validator("zones")
    @classmethod
    def _unique_reading_order(cls, zones: list[LayoutZone]) -> list[LayoutZone]:
        orders = [z.reading_order for z in zones]
        if len(orders) != len(set(orders)):
            raise ValueError("Duplicate reading_order values found in LayoutBlueprint")
        return zones


# ---------------------------------------------------------------------------
# PROMPT_2 output — anchored, repaired text per zone
# ---------------------------------------------------------------------------

class ZoneText(BaseModel):
    """Clean, unicode-repaired text anchored to a layout zone."""
    zone_id: str
    text: str = Field(..., description="Stitched, unicode-repaired string; no artificial line breaks")
    # FIX: carry zone_type forward so PROMPT_3 knows whether to emit H1-H6 vs P.
    zone_type: Optional["ZoneType"] = Field(
        None,
        description="Zone classification inherited from LayoutZone; guides semantic tag selection in PROMPT_3",
    )


class ZoneTextMap(BaseModel):
    """Full page text-to-zone mapping (PROMPT_2 output)."""
    page_number: int = Field(..., ge=1)
    zones: list[ZoneText] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared tag enumerations (used by PROMPT_3 / PROMPT_4 / PROMPT_5)
# ---------------------------------------------------------------------------

class PdfTag(str, Enum):
    H1         = "H1"
    H2         = "H2"
    H3         = "H3"
    H4         = "H4"
    H5         = "H5"
    H6         = "H6"
    P          = "P"
    L          = "L"
    LI         = "LI"
    LINK       = "Link"
    TABLE      = "Table"
    TR         = "TR"
    TH         = "TH"
    TD         = "TD"
    FIGURE     = "Figure"
    ARTIFACT   = "Artifact"


# ---------------------------------------------------------------------------
# PROMPT_3 / PROMPT_4 output — semantic tag tree
# ---------------------------------------------------------------------------

class TagNode(BaseModel):
    """One node in the semantic PDF tag tree."""
    tag: PdfTag
    zone_id: Optional[str] = Field(None, description="Source zone this node was derived from")
    content: Optional[str] = Field(None, description="Text content for leaf nodes")
    alt_text: Optional[str] = Field(None, description="Alt-text for Figure nodes (PROMPT_4)")
    is_artifact: bool = Field(False, description="True for running headers/footers and decorative images")
    children: list["TagNode"] = Field(default_factory=list)

    # Allow arbitrary extra fields from the LLM response without breaking validation.
    model_config = {"extra": "ignore"}


TagNode.model_rebuild()  # resolve forward reference


class TagTree(BaseModel):
    """Complete semantic tag structure for one page (PROMPT_3+4+5 output)."""
    page_number: int = Field(..., ge=1)
    nodes: list[TagNode] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# PROMPT_5 output — table matrix (merged back into TagTree)
# ---------------------------------------------------------------------------

class TableCell(BaseModel):
    """One cell in a parsed table."""
    row: int = Field(..., ge=0)
    col: int = Field(..., ge=0)
    is_header: bool = False
    col_span: int = Field(1, ge=1)
    row_span: int = Field(1, ge=1)
    content: str = ""
    header_ids: list[str] = Field(
        default_factory=list,
        description="IDs of TH cells this TD is associated with (PDF/UA scope mapping)"
    )


class TableMatrix(BaseModel):
    """Structured table parsed from a page (PROMPT_5 output)."""
    table_id: str = Field(..., description="Unique table identifier, e.g. 't1_p4'")
    page_number: int = Field(..., ge=1)
    is_continuation: bool = Field(
        False,
        description="True when this table continues from the previous page"
    )
    column_count: int = Field(..., ge=1)
    # FIX: PROMPT_5 needs header_row_count to correctly assign TH scope attributes.
    header_row_count: int = Field(
        1, ge=0,
        description="Number of leading rows that are header rows (TH); 0 = no headers detected",
    )
    cells: list[TableCell] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Cross-page state machine context (passed through PROMPT_3 and PROMPT_5)
# ---------------------------------------------------------------------------

class GlobalContext(BaseModel):
    """
    Mutable state dictionary threaded across all pages.
    Serialised and injected into PROMPT_3 and PROMPT_5 template strings.
    """
    current_heading_depth: int = Field(
        0, ge=0, le=6,
        description="The heading level (1–6) active at the end of the last page; 0 = none"
    )
    active_table_id: Optional[str] = Field(
        None,
        description="ID of a table that started on a previous page and has not yet closed"
    )
    active_table_column_count: Optional[int] = Field(
        None,
        description="Column count of the active cross-page table, for continuation alignment"
    )
    # FIX: cap at last 12 entries — sufficient for heading continuity detection
    # without unbounded growth on long documents.
    heading_history: list[int] = Field(
        default_factory=list,
        description="Last 12 heading depths seen (rolling window); used to detect illegal level skips",
        max_length=12,
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Extension slot for future per-document state"
    )

    def record_heading(self, depth: int) -> None:
        """Append a heading depth and enforce the 12-entry rolling window."""
        self.heading_history.append(depth)
        if len(self.heading_history) > 12:
            self.heading_history = self.heading_history[-12:]
