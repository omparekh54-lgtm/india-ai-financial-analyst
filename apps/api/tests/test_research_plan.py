from app.agents.contracts import AgentName
from app.orchestration.plan import AnalysisMode, build_research_plan


def test_full_plan_has_validation_before_synthesis() -> None:
    plan = build_research_plan(AnalysisMode.FULL)

    assert plan.stages[-2].agents == [AgentName.VALIDATOR]
    assert plan.stages[-1].agents == [AgentName.SYNTHESIS]
    assert plan.stages[1].parallel is True


def test_why_move_plan_is_selective() -> None:
    plan = build_research_plan(AnalysisMode.WHY_MOVE)
    collection = plan.stages[1].agents

    assert AgentName.MARKET in collection
    assert AgentName.NEWS in collection
    assert AgentName.FINANCIALS not in collection
