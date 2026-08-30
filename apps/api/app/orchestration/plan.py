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


class ExecutionStage(BaseModel):
    name: str
    agents: list[AgentName]
    parallel: bool = True


class ResearchPlan(BaseModel):
    mode: AnalysisMode
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


def build_research_plan(mode: AnalysisMode) -> ResearchPlan:
    if mode == AnalysisMode.WHY_MOVE:
        collection = [AgentName.MARKET, AgentName.NEWS, AgentName.WEB, AgentName.MACRO]
        analysis = [AgentName.TECHNICAL, AgentName.SENTIMENT, AgentName.RISK]
    elif mode == AnalysisMode.WHAT_CHANGED:
        collection = [
            AgentName.MARKET,
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
        collection = COLLECTION_AGENTS
        analysis = ANALYSIS_AGENTS

    return ResearchPlan(
        mode=mode,
        stages=[
            ExecutionStage(name="resolve", agents=[AgentName.ENTITY], parallel=False),
            ExecutionStage(name="collect", agents=collection, parallel=True),
            ExecutionStage(name="analyze", agents=analysis, parallel=True),
            ExecutionStage(name="validate", agents=[AgentName.VALIDATOR], parallel=False),
            ExecutionStage(name="synthesize", agents=[AgentName.SYNTHESIS], parallel=False),
        ],
    )
