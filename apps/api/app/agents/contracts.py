from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"
    ENTITY = "security_entity_intelligence"
    MARKET = "live_market_microstructure"
    FINANCIALS = "financial_forensic_accounting"
    FILINGS = "filings_governance_corporate_actions"
    EARNINGS = "earnings_management_intelligence"
    NEWS = "news_event_intelligence"
    WEB = "web_intelligence"
    INDUSTRY = "industry_peer_intelligence"
    MACRO = "india_macro_policy_flow"
    VALUATION = "valuation_scenario_engine"
    TECHNICAL = "technical_derivatives_intelligence"
    SENTIMENT = "sentiment_narrative_intelligence"
    RISK = "risk_red_flag_intelligence"
    VALIDATOR = "evidence_cross_validation"
    SYNTHESIS = "chief_analyst_research_composer"


class EvidenceRef(BaseModel):
    evidence_id: UUID = Field(default_factory=uuid4)
    source_type: str
    source_uri: str
    title: str | None = None
    published_at: str | None = None
    retrieved_at: str
    freshness: Literal["live", "near_live", "periodic", "historical", "unknown"] = "unknown"
    excerpt: str | None = None
    checksum: str | None = None


class Claim(BaseModel):
    claim_id: UUID = Field(default_factory=uuid4)
    agent: AgentName
    statement: str
    claim_type: Literal["fact", "calculation", "inference", "scenario", "risk", "catalyst"]
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[UUID] = Field(default_factory=list)
    status: Literal[
        "pending", "verified", "supported", "contested", "inferred", "unsupported", "stale"
    ] = "pending"
    data: dict[str, Any] = Field(default_factory=dict)


class AgentInput(BaseModel):
    job_id: UUID
    security_id: UUID | None = None
    query: str
    context: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class AgentOutput(BaseModel):
    agent: AgentName
    ok: bool = True
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
