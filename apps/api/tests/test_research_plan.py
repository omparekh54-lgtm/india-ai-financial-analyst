from app.agents.contracts import AgentName
from app.orchestration.plan import (
    AnalysisMode,
    EventTrigger,
    ResearchDepth,
    build_event_research_plan,
    build_research_plan,
)


def test_standard_full_plan_has_validation_before_synthesis() -> None:
    plan = build_research_plan(AnalysisMode.FULL)

    assert plan.depth == ResearchDepth.STANDARD
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]
    assert plan.stages[1].parallel is True
    assert AgentName.WEB in plan.stages[1].agents
    assert AgentName.SENTIMENT in plan.stages[2].agents


def test_quick_plan_is_materially_smaller_but_keeps_validation_gate() -> None:
    plan = build_research_plan(AnalysisMode.FULL, ResearchDepth.QUICK)
    collection = plan.stages[1].agents
    analysis = plan.stages[2].agents

    assert collection == [
        AgentName.MARKET,
        AgentName.FINANCIALS,
        AgentName.FILINGS,
        AgentName.EARNINGS,
        AgentName.NEWS,
    ]
    assert analysis == [AgentName.TECHNICAL, AgentName.RISK]
    assert AgentName.WEB not in collection
    assert AgentName.INDUSTRY not in collection
    assert AgentName.MACRO not in collection
    assert AgentName.VALUATION not in analysis
    assert AgentName.SENTIMENT not in analysis
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]


def test_deep_plan_keeps_full_specialist_set() -> None:
    plan = build_research_plan(AnalysisMode.FULL, ResearchDepth.DEEP)

    assert plan.depth == ResearchDepth.DEEP
    assert AgentName.WEB in plan.stages[1].agents
    assert AgentName.SENTIMENT in plan.stages[2].agents
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]


def test_what_changed_manual_fallback_recomputes_financial_and_valuation_inputs() -> None:
    plan = build_research_plan(AnalysisMode.WHAT_CHANGED)

    assert AgentName.MARKET in plan.stages[1].agents
    assert AgentName.FINANCIALS in plan.stages[1].agents
    assert AgentName.FILINGS in plan.stages[1].agents
    assert AgentName.EARNINGS in plan.stages[1].agents
    assert AgentName.VALUATION in plan.stages[2].agents
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]


def test_why_move_plan_includes_peer_attribution() -> None:
    plan = build_research_plan(AnalysisMode.WHY_MOVE)
    collection = plan.stages[1].agents

    assert AgentName.MARKET in collection
    assert AgentName.NEWS in collection
    assert AgentName.INDUSTRY in collection
    assert AgentName.MACRO in collection
    assert AgentName.FINANCIALS not in collection


def test_quarterly_result_event_runs_smallest_safe_subgraph() -> None:
    plan = build_event_research_plan(EventTrigger.QUARTERLY_RESULT)

    assert plan.stages[1].agents == [AgentName.FINANCIALS, AgentName.EARNINGS]
    assert plan.stages[2].agents == [AgentName.VALUATION, AgentName.RISK]
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]


def test_governance_event_does_not_rerun_unrelated_agents() -> None:
    plan = build_event_research_plan(EventTrigger.GOVERNANCE_FILING)

    assert plan.stages[1].agents == [AgentName.FILINGS]
    assert plan.stages[2].agents == [AgentName.RISK]
    assert AgentName.MARKET not in plan.stages[1].agents
    assert AgentName.VALUATION not in plan.stages[2].agents


def test_no_material_change_reuses_fundamentals_and_updates_market_layer_only() -> None:
    plan = build_event_research_plan(EventTrigger.NO_MATERIAL_CHANGE)

    assert plan.stages[1].agents == [AgentName.MARKET]
    assert plan.stages[2].agents == [AgentName.TECHNICAL]
