from uuid import UUID, uuid4

import pytest

from app.agents.contracts import AgentInput, AgentName, AgentOutput
from app.orchestration.plan import AnalysisMode, ExecutionStage, ResearchDepth, ResearchPlan
from app.orchestration.runtime import AgentRegistry, OrchestratorRuntime


class FakeAgent:
    def __init__(self, name: AgentName) -> None:
        self.name = name

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        return AgentOutput(agent=self.name, metrics={"query": agent_input.query})


class ResolvingAgent:
    def __init__(self, security_id: UUID) -> None:
        self.security_id = security_id

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        return AgentOutput(
            agent=AgentName.ENTITY,
            metrics={"security": {"id": str(self.security_id), "legal_name": "Example Ltd"}},
        )


class RecordingContextLoader:
    def __init__(self) -> None:
        self.user_id: UUID | None = None
        self.depth: str | None = None

    async def load(
        self,
        security_id: UUID,
        *,
        mode: str,
        depth: str,
        user_id: UUID | None = None,
    ):
        self.user_id = user_id
        self.depth = depth
        return {
            "hydrated_security_id": str(security_id),
            "mode": mode,
            "depth": depth,
        }, []


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


@pytest.mark.asyncio
async def test_runtime_passes_authenticated_user_and_depth_to_context_loader() -> None:
    security_id = uuid4()
    user_id = uuid4()
    loader = RecordingContextLoader()
    registry = AgentRegistry(
        {
            AgentName.ENTITY: ResolvingAgent(security_id),
            AgentName.MARKET: FakeAgent(AgentName.MARKET),
        }
    )
    plan = ResearchPlan(
        mode=AnalysisMode.FULL,
        depth=ResearchDepth.DEEP,
        stages=[
            ExecutionStage(name="resolve", agents=[AgentName.ENTITY], parallel=False),
            ExecutionStage(name="collect", agents=[AgentName.MARKET], parallel=False),
        ],
    )

    await OrchestratorRuntime(registry, context_loader=loader).run(
        plan,
        AgentInput(job_id=uuid4(), user_id=user_id, query="EXAMPLE"),
    )

    assert loader.user_id == user_id
    assert loader.depth == "deep"
