from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol

from app.agents.contracts import AgentInput, AgentName, AgentOutput
from app.orchestration.plan import ResearchPlan


class AgentHandler(Protocol):
    async def run(self, agent_input: AgentInput) -> AgentOutput: ...


class MissingAgentError(RuntimeError):
    pass


class AgentRegistry:
    def __init__(self, handlers: Mapping[AgentName, AgentHandler] | None = None) -> None:
        self.handlers: dict[AgentName, AgentHandler] = dict(handlers or {})

    def register(self, agent: AgentName, handler: AgentHandler) -> None:
        self.handlers[agent] = handler

    def get(self, agent: AgentName) -> AgentHandler:
        try:
            return self.handlers[agent]
        except KeyError as exc:
            raise MissingAgentError(f"No handler registered for {agent.value}") from exc


class OrchestratorRuntime:
    """Executes a research DAG without allowing agents to silently bypass validation."""

    def __init__(self, registry: AgentRegistry, *, max_concurrency: int = 6) -> None:
        self.registry = registry
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def run(self, plan: ResearchPlan, agent_input: AgentInput) -> list[AgentOutput]:
        outputs: list[AgentOutput] = []
        working_input = agent_input.model_copy(deep=True)

        for stage in plan.stages:
            if stage.name == "validate":
                working_input.context["candidate_claims"] = [
                    claim.model_dump(mode="json")
                    for output in outputs
                    for claim in output.claims
                ]
                working_input.evidence = _dedupe_evidence(outputs)

            if stage.parallel:
                stage_outputs = await asyncio.gather(
                    *(self._run_one(agent, working_input) for agent in stage.agents)
                )
            else:
                stage_outputs = []
                for agent in stage.agents:
                    stage_outputs.append(await self._run_one(agent, working_input))

            outputs.extend(stage_outputs)
            working_input.context.setdefault("agent_outputs", {}).update(
                {
                    output.agent.value: output.model_dump(mode="json")
                    for output in stage_outputs
                }
            )

        return outputs

    async def _run_one(self, agent: AgentName, agent_input: AgentInput) -> AgentOutput:
        handler = self.registry.get(agent)
        async with self.semaphore:
            return await handler.run(agent_input.model_copy(deep=True))


def _dedupe_evidence(outputs: list[AgentOutput]):
    evidence = {}
    for output in outputs:
        for item in output.evidence:
            evidence[item.evidence_id] = item
    return list(evidence.values())
