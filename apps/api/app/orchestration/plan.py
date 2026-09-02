from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.agents.contracts import AgentName


class AnalysisMode(StrEnum):
    FULL = "full_analysis"
    WHY_MOVE = "why_did_it_move"
    WHAT_CHANGED = "what_changed"
    FUNDAMENTALS = "fundamentals"
    RISK = "risk"


class ResearchDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class EventTrigger(StrEnum):
    QUARTERLY_RESULT = "quarterly_result"
    LARGE_PRICE_MOVE = "large_price_move"
    GOVERNANCE_FILING = "governance_filing"
    RBI_POLICY = "rbi_policy"
    ANNUAL_REPORT = "annual_report"
    NO_MATERIAL_CHANGE = "no_material_change"


class ExecutionStage(BaseModel):
    name: str
    agents: list[AgentName]
    parallel: bool = True


class ResearchPlan(BaseModel):
    mode: AnalysisMode
    depth: ResearchDepth = ResearchDepth.STANDARD
    stages: list[ExecutionStage]


COLLECTION_AGENTS = [
    AgentName.MARKET,
    AgentName.FINANCIALS,
    AgentName.FILINGS,
    AgentName.EARNINGS,
    AgentName.NEWS,
    AgentName.WEB,
    AgentName.INDUSTRY,
    AgentName.MACRO,
]

ANALYSIS_AGENTS = [
    AgentName.VALUATION,
    AgentName.TECHNICAL,
    AgentName.SENTIMENT,
    AgentName.RISK,
]


EVENT_AGENT_MAP: dict[EventTrigger, tuple[list[AgentName], list[AgentName]]] = {
    EventTrigger.QUARTERLY_RESULT: (
        [AgentName.FINANCIALS, AgentName.EARNINGS],
        [AgentName.VALUATION, AgentName.RISK],
    ),
    EventTrigger.LARGE_PRICE_MOVE: (
        [AgentName.MARKET, AgentName.NEWS, AgentName.INDUSTRY, AgentName.MACRO],
        [AgentName.TECHNICAL, AgentName.SENTIMENT],
    ),
    EventTrigger.GOVERNANCE_FILING: (
        [AgentName.FILINGS],
        [AgentName.RISK],
    ),
    EventTrigger.RBI_POLICY: (
        [AgentName.MACRO, AgentName.INDUSTRY],
        [AgentName.RISK],
    ),
    EventTrigger.ANNUAL_REPORT: (
        [
            AgentName.FINANCIALS,
            AgentName.FILINGS,
            AgentName.EARNINGS,
            AgentName.INDUSTRY,
        ],
        [AgentName.VALUATION, AgentName.RISK],
    ),
    EventTrigger.NO_MATERIAL_CHANGE: (
        [AgentName.MARKET],
        [AgentName.TECHNICAL],
    ),
}


def build_research_plan(
    mode: AnalysisMode,
    depth: ResearchDepth = ResearchDepth.STANDARD,
) -> ResearchPlan:
    if mode == AnalysisMode.WHY_MOVE:
        collection = [
            AgentName.MARKET,
            AgentName.NEWS,
            AgentName.WEB,
            AgentName.INDUSTRY,
            AgentName.MACRO,
        ]
        analysis = [AgentName.TECHNICAL, AgentName.SENTIMENT, AgentName.RISK]
    elif mode == AnalysisMode.WHAT_CHANGED:
        # This is the safe manual fallback when no machine-classified event trigger is supplied.
        # Automated refreshes should use build_event_research_plan() for the smallest safe DAG.
        collection = [
            AgentName.MARKET,
            AgentName.FINANCIALS,
            AgentName.FILINGS,
            AgentName.EARNINGS,
            AgentName.NEWS,
            AgentName.WEB,
        ]
        analysis = [AgentName.VALUATION, AgentName.SENTIMENT, AgentName.RISK]
    elif mode == AnalysisMode.FUNDAMENTALS:
        collection = [
            AgentName.FINANCIALS,
            AgentName.FILINGS,
            AgentName.EARNINGS,
            AgentName.INDUSTRY,
            AgentName.MACRO,
        ]
        analysis = [AgentName.VALUATION, AgentName.RISK]
    elif mode == AnalysisMode.RISK:
        collection = [
            AgentName.FINANCIALS,
            AgentName.FILINGS,
            AgentName.EARNINGS,
            AgentName.NEWS,
            AgentName.WEB,
            AgentName.MACRO,
        ]
        analysis = [AgentName.SENTIMENT, AgentName.RISK]
    else:
        collection = list(COLLECTION_AGENTS)
        analysis = list(ANALYSIS_AGENTS)

    if depth == ResearchDepth.QUICK:
        if mode == AnalysisMode.FULL:
            collection = [
                AgentName.MARKET,
                AgentName.FINANCIALS,
                AgentName.FILINGS,
                AgentName.EARNINGS,
                AgentName.NEWS,
            ]
            analysis = [AgentName.TECHNICAL, AgentName.RISK]
        else:
            collection = [agent for agent in collection if agent != AgentName.WEB]
            analysis = [agent for agent in analysis if agent != AgentName.SENTIMENT]

    return _build_plan(mode=mode, depth=depth, collection=collection, analysis=analysis)


def build_event_research_plan(
    trigger: EventTrigger,
    depth: ResearchDepth = ResearchDepth.STANDARD,
) -> ResearchPlan:
    """Map a classified new event to the smallest safe v2 dependency subgraph."""
    collection, analysis = EVENT_AGENT_MAP[trigger]
    return _build_plan(
        mode=AnalysisMode.WHAT_CHANGED,
        depth=depth,
        collection=list(collection),
        analysis=list(analysis),
    )


def _build_plan(
    *,
    mode: AnalysisMode,
    depth: ResearchDepth,
    collection: list[AgentName],
    analysis: list[AgentName],
) -> ResearchPlan:
    return ResearchPlan(
        mode=mode,
        depth=depth,
        stages=[
            ExecutionStage(name="resolve", agents=[AgentName.ENTITY], parallel=False),
            ExecutionStage(name="collect", agents=collection, parallel=True),
            ExecutionStage(name="analyze", agents=analysis, parallel=True),
            ExecutionStage(name="validate", agents=[AgentName.VALIDATOR], parallel=False),
            ExecutionStage(name="synthesize", agents=[AgentName.SYNTHESIS], parallel=False),
        ],
    )
