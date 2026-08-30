from __future__ import annotations

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim


POSITIVE = {
    "strong",
    "robust",
    "accelerating",
    "improved",
    "improving",
    "growth",
    "upgrade",
    "record",
    "beat",
    "healthy",
    "confidence",
    "opportunity",
}
NEGATIVE = {
    "weak",
    "slowing",
    "decline",
    "downgrade",
    "miss",
    "pressure",
    "uncertain",
    "risk",
    "default",
    "fraud",
    "resignation",
    "loss",
}


class SentimentNarrativeAgent:
    """A transparent baseline sentiment layer; LLM narrative analysis can augment it later."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        narratives = agent_input.context.get("narratives") or []
        if not narratives:
            narratives = _collect_from_outputs(agent_input.context.get("agent_outputs") or {})
        if not narratives:
            return AgentOutput(
                agent=AgentName.SENTIMENT,
                ok=False,
                warnings=["No narrative text supplied"],
            )

        text = " ".join(str(item) for item in narratives).lower()
        tokens = [token.strip(".,:;!?()[]{}\"'") for token in text.split()]
        positive = sum(token in POSITIVE for token in tokens)
        negative = sum(token in NEGATIVE for token in tokens)
        total = positive + negative
        score = 0.0 if total == 0 else (positive - negative) / total
        label = "positive" if score >= 0.20 else "negative" if score <= -0.20 else "neutral"

        claim = Claim(
            agent=AgentName.SENTIMENT,
            statement=f"Observed narrative sentiment is {label}",
            claim_type="inference",
            confidence=min(0.90, 0.55 + total * 0.02),
            status="inferred",
            data={"score": score, "positive_terms": positive, "negative_terms": negative},
        )
        return AgentOutput(
            agent=AgentName.SENTIMENT,
            claims=[claim],
            metrics={
                "sentiment_score": score,
                "sentiment_label": label,
                "positive_terms": positive,
                "negative_terms": negative,
                "narrative_count": len(narratives),
            },
        )


def _collect_from_outputs(outputs: dict[str, object]) -> list[str]:
    narratives: list[str] = []
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        for claim in output.get("claims", []):
            if isinstance(claim, dict) and claim.get("statement"):
                narratives.append(str(claim["statement"]))
    return narratives
