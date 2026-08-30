from __future__ import annotations

from pydantic import BaseModel, Field


class SecurityRecord(BaseModel):
    legal_name: str
    nse_symbol: str | None = None
    bse_code: str | None = None
    isin: str | None = None
    sector: str | None = None
    industry: str | None = None
    primary_exchange: str | None = None
    aliases: list[str] = Field(default_factory=list)
    provider_instruments: dict[str, str] = Field(default_factory=dict)


class ResolveCandidate(BaseModel):
    security: SecurityRecord
    score: float = Field(ge=0, le=1)
    match_reason: str


class ResolveResult(BaseModel):
    query: str
    normalized_query: str
    resolved: bool
    candidate: ResolveCandidate | None = None
    alternatives: list[ResolveCandidate] = Field(default_factory=list)
