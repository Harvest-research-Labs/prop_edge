"""Shared Pydantic request/response schemas for the ProEdge API gateway."""
from typing import Any, Optional
from pydantic import BaseModel, Field


# ---- response envelope (every endpoint returns this) -----------------------
class ApiResponse(BaseModel):
    request_id: str
    service_version: str
    model_version: str
    data_timestamp: str
    status: str                       # ok | partial | error | unavailable
    source: str
    confidence: Optional[float] = None
    warnings: list[str] = []
    errors: list[str] = []
    data: Any = None


# ---- 1. extract ------------------------------------------------------------
class ExtractRequest(BaseModel):
    image_b64: str = Field(..., description="Base64-encoded screenshot bytes")
    media_type: str = "image/png"
    platform_hint: Optional[str] = None


# ---- 2. resolve ------------------------------------------------------------
class RawPickIn(BaseModel):
    player: str
    stat: str
    line: Optional[float] = None
    side: str = "more"
    sport: Optional[str] = None
    team: Optional[str] = None
    opponent: Optional[str] = None
    event_id: Optional[str] = None
    event_start: Optional[str] = None
    entity_id: Optional[str] = None       # lets a user correction resolve exactly


class ResolveRequest(BaseModel):
    picks: list[RawPickIn]


# ---- 3. project ------------------------------------------------------------
class ProjectPickIn(BaseModel):
    player: str
    stat: str
    sport: str
    team: Optional[str] = None
    opponent: Optional[str] = None


class ProjectRequest(BaseModel):
    picks: list[ProjectPickIn]


# ---- 4. price --------------------------------------------------------------
class PricePickIn(BaseModel):
    stat: str
    line: float
    side: str = "more"
    mean: Optional[float] = None
    market_over_odds: Optional[int] = None    # American odds (Underdog-style)
    market_under_odds: Optional[int] = None


class PriceRequest(BaseModel):
    picks: list[PricePickIn]


# ---- 5. rank ---------------------------------------------------------------
class RankRequest(BaseModel):
    rows: list[dict] = Field(..., description="Annotated rows (as from projections.annotate)")
    limit: int = 24


# ---- 6. slip/eval ----------------------------------------------------------
class LegIn(BaseModel):
    player: str
    stat: str
    line: Optional[float] = None
    side: str = "more"
    prob: float
    flavor: str = "standard"
    sport: Optional[str] = None


class SlipRequest(BaseModel):
    legs: list[LegIn]


# ---- 7. explain ------------------------------------------------------------
class ExplainRequest(BaseModel):
    rows: list[dict]
    question: str
