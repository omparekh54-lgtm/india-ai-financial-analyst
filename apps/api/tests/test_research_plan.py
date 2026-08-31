from app.agents.contracts import AgentName
from app.orchestration.plan import AnalysisMode, ResearchDepth, build_research_plan


def test_standard_full_plan_has_validation_before_synthesis() -> None:
    plan = build_research_plan(AnalysisMode.FULL)

    assert plan.depth == ResearchDepth.STANDARD
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]
    assert plan.stages[1].parallel is True
    assert AgentName.WEB in plan.stages[1].agents
    assert AgentName.SENTIMENT in plan.stages[2].agents


def test_quick_plan_prunes_optional_agents_but_keeps_validation_gate() -> None:
    plan = build_research_plan(AnalysisMode.FULL, ResearchDepth.QUICK)

    assert AgentName.WEB not in plan.stages[1].agents
    assert AgentName.SENTIMENT not in plan.stages[2].agents
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]


def test_deep_plan_keeps_full_specialist_set() -> None:
    plan = build_research_plan(AnalysisMode.FULL, ResearchDepth.DEEP)

    assert plan.depth == ResearchDepth.DEEP
    assert AgentName.WEB in plan.stages[1].agents
    assert AgentName.SENTIMENT in plan.stages[2].agents
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]


def test_what_changed_recomputes_financial_and_valuation_inputs() -> None:
    plan = build_research_plan(AnalysisMode.WHAT_CHANGED)

    assert AgentName.MARKET in plan.stages[1].agents
    assert AgentName.FINANCIALS in plan.stages[1].agents
    assert AgentName.FILINGS in plan.stages[1].agents
    assert AgentName.EARNINGS in plan.stages[1].agents
    assert AgentName.VALUATION in plan.stages[2].agents
    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]


def test_why_move_plan_is_selective() -> None:
    plan = build_research_plan(AnalysisMode.WHY_MOVE)
    collection = plan.stages[1].agents

    assert AgentName.MARKET in collection
    assert AgentName.NEWS in collection
    assert AgentName.FINANCIALS not in collection
