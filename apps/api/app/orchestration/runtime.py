from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from app.agents.contracts import AgentInput, AgentName, AgentOutput, EvidenceRef
from app.orchestration.plan import ResearchPlan


class AgentHandler(Protocol):
    async def run(self, agent_input: AgentInput) -> AgentOutput: ...


class ContextLoader(Protocol):
    async def load(
        self,
        security_id: UUID,
        *,
        mode: str,
        depth: str,
        user_id: UUID | None = None,
    ) -> tuple[dict[str, object], list[EvidenceRef]]: ...


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
    """Executes the research DAG while preserving evidence and validation boundaries."""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        max_concurrency: int = 6,
        context_loader: ContextLoader | None = None,
    ) -> None:
        self.registry = registry
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.context_loader = context_loader

    async def run(self, plan: ResearchPlan, agent_input: AgentInput) -> list[AgentOutput]:
        outputs: list[AgentOutput] = []
        working_input = agent_input.model_copy(deep=True)
        working_input.context["analysis_mode"] = plan.mode.value
        working_input.context["analysis_depth"] = plan.depth.value

        for stage in plan.stages:
            if stage.name == "validate":
                working_input.context["candidate_claims"] = [
                    claim.model_dump(mode="json")
                    for output in outputs
                    for claim in output.claims
                    if output.agent != AgentName.VALIDATOR
                ]

            if stage.name == "synthesize":
                validator_output = _latest_output(outputs, AgentName.VALIDATOR)
                if validator_output is None:
                    raise RuntimeError("Synthesis cannot run before evidence validation")
                working_input.context["validated_claims"] = [
                    claim.model_dump(mode="json")
                    for claim in validator_output.claims
                    if claim.status in {"verified", "supported", "inferred"}
                ]
                working_input.context["validation_metrics"] = validator_output.metrics
                working_input.context["validation_warnings"] = validator_output.warnings

            if stage.parallel:
                stage_outputs = await asyncio.gather(
                    *(self._run_one(agent, working_input) for agent in stage.agents)
                )
            else:
                stage_outputs = []
                for agent in stage.agents:
                    stage_outputs.append(await self._run_one(agent, working_input))

            outputs.extend(stage_outputs)
            working_input.evidence = _dedupe_evidence(
                [*working_input.evidence, *(item for output in stage_outputs for item in output.evidence)]
            )
            working_input.context.setdefault("agent_outputs", {}).update(
                {
                    output.agent.value: output.model_dump(mode="json")
                    for output in stage_outputs
                }
            )
            _promote_stage_metrics(working_input.context, stage_outputs)

            if stage.name == "resolve":
                await self._hydrate_after_resolution(working_input, plan)

        return outputs

    async def _hydrate_after_resolution(
        self,
        working_input: AgentInput,
        plan: ResearchPlan,
    ) -> None:
        entity_output = working_input.context.get("agent_outputs", {}).get(AgentName.ENTITY.value)
        if not isinstance(entity_output, dict):
            return
        metrics = entity_output.get("metrics") or {}
        if not isinstance(metrics, dict):
            return
        security = metrics.get("security") or {}
        if not isinstance(security, dict) or not security.get("id"):
            return

        working_input.security_id = UUID(str(security["id"]))
        working_input.context["security"] = security
        if self.context_loader is None:
            return

        loaded_context, loaded_evidence = await self.context_loader.load(
            working_input.security_id,
            mode=plan.mode.value,
            depth=plan.depth.value,
            user_id=working_input.user_id,
        )
        working_input.context.update(loaded_context)
        working_input.evidence = _dedupe_evidence([*working_input.evidence, *loaded_evidence])

    async def _run_one(self, agent: AgentName, agent_input: AgentInput) -> AgentOutput:
        handler = self.registry.get(agent)
        async with self.semaphore:
            return await handler.run(agent_input.model_copy(deep=True))


def _promote_stage_metrics(context: dict[str, object], outputs: list[AgentOutput]) -> None:
    mappings = {
        AgentName.MARKET: "market_metrics",
        AgentName.FINANCIALS: "financial_metrics",
        AgentName.FILINGS: "filing_metrics",
        AgentName.EARNINGS: "earnings_metrics",
        AgentName.INDUSTRY: "industry_metrics",
        AgentName.MACRO: "macro_metrics",
        AgentName.VALUATION: "valuation_metrics",
        AgentName.TECHNICAL: "technical_metrics",
    }
    for output in outputs:
        key = mappings.get(output.agent)
        if key:
            context[key] = output.metrics


def _latest_output(outputs: list[AgentOutput], agent: AgentName) -> AgentOutput | None:
    for output in reversed(outputs):
        if output.agent == agent:
            return output
    return None


def _dedupe_evidence(items: list[EvidenceRef]) -> list[EvidenceRef]:
    evidence: dict[object, EvidenceRef] = {}
    for item in items:
        evidence[item.evidence_id] = item
    return list(evidence.values())
