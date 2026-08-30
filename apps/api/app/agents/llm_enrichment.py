from __future__ import annotations

import json
from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim, EvidenceRef
from app.providers.client import ChatMessage, ProviderCallError
from app.providers.gateway import ProviderGateway
from app.providers.router import Capability


class AgentHandler(Protocol):
    async def run(self, agent_input: AgentInput) -> AgentOutput: ...


class EnrichmentInsight(BaseModel):
    statement: str = Field(min_length=8, max_length=600)
    claim_type: Literal["inference", "risk", "catalyst"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_keys: list[str] = Field(default_factory=list, max_length=6)


class EnrichmentBundle(BaseModel):
    insights: list[EnrichmentInsight] = Field(default_factory=list, max_length=3)


class SynthesisNarrative(BaseModel):
    executive_summary: str = Field(min_length=20, max_length=2400)
    bull_case: list[str] = Field(default_factory=list, max_length=3)
    bear_case: list[str] = Field(default_factory=list, max_length=3)
    watch_items: list[str] = Field(default_factory=list, max_length=5)
    confidence_note: str = Field(default="", max_length=700)


class LlmEnrichedAgent:
    """Adds bounded qualitative insights after deterministic agent logic.

    The wrapper never replaces the deterministic output. Any new LLM insight must
    cite evidence supplied to the model and remains pending until Agent 15 validates it.
    """

    def __init__(
        self,
        inner: AgentHandler,
        *,
        agent: AgentName,
        gateway: ProviderGateway,
        capability: Capability,
        max_evidence: int = 8,
    ) -> None:
        self.inner = inner
        self.agent = agent
        self.gateway = gateway
        self.capability = capability
        self.max_evidence = max(2, min(max_evidence, 12))

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        base = await self.inner.run(agent_input)
        if not self.gateway.enabled:
            return base

        evidence = _select_evidence(base.evidence or agent_input.evidence, self.max_evidence)
        if not evidence:
            return base

        evidence_keys = {f"E{index + 1}": item for index, item in enumerate(evidence)}
        prompt = _enrichment_prompt(
            agent=self.agent,
            query=agent_input.query,
            base=base,
            evidence=evidence_keys,
        )
        try:
            result = await self.gateway.complete(
                self.capability,
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "You are an evidence-constrained Indian equity research annotator. "
                            "Return JSON only. Never invent facts, figures, dates, causes, or events. "
                            "Use only the supplied evidence and deterministic metrics."
                        ),
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                temperature=0.1,
                max_tokens=1200,
            )
            bundle = EnrichmentBundle.model_validate(_json_object(result.content))
        except (ProviderCallError, ValidationError, ValueError, json.JSONDecodeError) as exc:
            return base.model_copy(
                update={
                    "warnings": [
                        *base.warnings,
                        f"LLM enrichment skipped ({type(exc).__name__})",
                    ]
                }
            )

        added: list[Claim] = []
        for insight in bundle.insights:
            linked = [
                evidence_keys[key].evidence_id
                for key in insight.evidence_keys
                if key in evidence_keys
            ]
            linked = list(dict.fromkeys(linked))
            if not linked:
                continue
            added.append(
                Claim(
                    agent=self.agent,
                    statement=insight.statement.strip(),
                    claim_type=insight.claim_type,
                    confidence=min(float(insight.confidence), 0.78),
                    evidence_ids=linked,
                    status="pending",
                    data={
                        "origin": "llm_enrichment",
                        "provider": result.provider,
                        "model": result.model,
                    },
                )
            )

        metrics = dict(base.metrics)
        metrics["llm_enrichment"] = {
            "provider": result.provider,
            "model": result.model,
            "insight_count": len(added),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
        return base.model_copy(
            update={
                "claims": [*base.claims, *added],
                "metrics": metrics,
            }
        )


class LlmSynthesisAgent:
    """Adds readable prose using only claims already admitted by Agent 15."""

    def __init__(self, inner: AgentHandler, gateway: ProviderGateway) -> None:
        self.inner = inner
        self.gateway = gateway

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        base = await self.inner.run(agent_input)
        if not base.ok or not self.gateway.enabled:
            return base

        report = base.metrics.get("report")
        if not isinstance(report, dict):
            return base
        raw_claims = agent_input.context.get("validated_claims") or []
        claims = [
            claim if isinstance(claim, Claim) else Claim.model_validate(claim)
            for claim in raw_claims
        ]
        admitted = [
            claim
            for claim in claims
            if claim.status in {"verified", "supported", "inferred"}
        ][:48]
        if not admitted:
            return base

        prompt = _synthesis_prompt(agent_input.query, report, admitted)
        try:
            result = await self.gateway.complete(
                Capability.DEEP_REASONING,
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "You are the chief research editor. Summarize only validated claims. "
                            "Do not add facts, prices, targets, dates, forecasts, or causal statements "
                            "that are absent from the supplied claims. Return JSON only."
                        ),
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                temperature=0.15,
                max_tokens=1800,
            )
            narrative = SynthesisNarrative.model_validate(_json_object(result.content))
        except (ProviderCallError, ValidationError, ValueError, json.JSONDecodeError) as exc:
            return base.model_copy(
                update={
                    "warnings": [
                        *base.warnings,
                        f"LLM synthesis narrative skipped ({type(exc).__name__})",
                    ]
                }
            )

        updated_report = dict(report)
        updated_report["executive_summary"] = narrative.executive_summary
        updated_report["narrative"] = {
            "bull_case": narrative.bull_case,
            "bear_case": narrative.bear_case,
            "watch_items": narrative.watch_items,
            "confidence_note": narrative.confidence_note,
            "provider": result.provider,
            "model": result.model,
        }
        metrics = dict(base.metrics)
        metrics["report"] = updated_report
        metrics["llm_synthesis"] = {
            "provider": result.provider,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
        return base.model_copy(update={"metrics": metrics})


def _select_evidence(items: list[EvidenceRef], limit: int) -> list[EvidenceRef]:
    ranked = sorted(
        items,
        key=lambda item: (
            item.source_priority,
            0 if item.excerpt else 1,
            0 if item.freshness in {"live", "near_live", "periodic"} else 1,
        ),
    )
    selected: list[EvidenceRef] = []
    seen: set[tuple[str, str, int | None]] = set()
    for item in ranked:
        if not item.excerpt:
            continue
        key = (item.source_type, item.source_uri, item.page_number)
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _enrichment_prompt(
    *,
    agent: AgentName,
    query: str,
    base: AgentOutput,
    evidence: dict[str, EvidenceRef],
) -> str:
    deterministic_claims = [claim.statement for claim in base.claims[:12]]
    metric_json = json.dumps(base.metrics, default=str, ensure_ascii=False)[:7000]
    evidence_text = "\n\n".join(
        (
            f"{key} | type={item.source_type} | priority={item.source_priority} | "
            f"title={item.title or 'untitled'} | page={item.page_number or '-'}\n"
            f"{(item.excerpt or '')[:1400]}"
        )
        for key, item in evidence.items()
    )
    return (
        f"Research query: {query}\n"
        f"Agent: {agent.value}\n\n"
        "Deterministic claims already produced (do not merely restate them):\n"
        + "\n".join(f"- {statement}" for statement in deterministic_claims)
        + f"\n\nDeterministic metrics:\n{metric_json}\n\n"
        f"Evidence:\n{evidence_text}\n\n"
        "Return exactly this JSON shape:\n"
        '{"insights":[{"statement":"...","claim_type":"inference|risk|catalyst",'
        '"confidence":0.0,"evidence_keys":["E1"]}]}\n'
        "Rules: maximum 3 insights; every insight must cite at least one evidence key; "
        "do not infer exact numbers not present in evidence; do not give buy/sell instructions."
    )


def _synthesis_prompt(query: str, report: dict[str, object], claims: list[Claim]) -> str:
    claim_lines = "\n".join(
        f"C{index + 1} [{claim.status}/{claim.confidence:.2f}] {claim.statement}"
        for index, claim in enumerate(claims)
    )
    confidence = json.dumps(report.get("confidence") or {}, default=str)
    return (
        f"Research query: {query}\n"
        f"Confidence framework: {confidence}\n\n"
        f"Validated claims:\n{claim_lines}\n\n"
        "Return exactly this JSON shape:\n"
        '{"executive_summary":"...","bull_case":["..."],"bear_case":["..."],'
        '"watch_items":["..."],"confidence_note":"..."}\n'
        "Write concise institutional-style prose. Bull/bear/watch items must be traceable to the "
        "claims above. If evidence is mixed or thin, say so explicitly."
    )


def _json_object(content: str) -> object:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Provider response did not contain a JSON object")
    return json.loads(cleaned[start : end + 1])
