from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, AgentName, AgentOutput
from app.orchestration.plan import ExecutionStage, ResearchPlan, AnalysisMode
from app.orchestration.runtime import AgentRegistry, OrchestratorRuntime


class FakeAgent:
    def __init__(self, name: AgentName) -> None:
        self.name = name

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        return AgentOutput(agent=self.name, metrics={"query": agent_input.query})


@pytest.mark.asyncio
async def test_runtime_executes_stages_in_order() -> None:
    registry = AgentRegistry(
        {
            AgentName.ENTITY: FakeAgent(AgentName.ENTITY),
            AgentName.MARKET: FakeAgent(AgentName.MARKET),
        }
    )
    plan = ResearchPlan(
        mode=AnalysisMode.FULL,
        stages=[
            ExecutionStage(name="resolve", agents=[AgentName.ENTITY], parallel=False),
            ExecutionStage(name="collect", agents=[AgentName.MARKET], parallel=True),
        ],
    )
    outputs = await OrchestratorRuntime(registry).run(
        plan,
        AgentInput(job_id=uuid4(), query="EXAMPLE"),
    )

    assert [output.agent for output in outputs] == [AgentName.ENTITY, AgentName.MARKET]
