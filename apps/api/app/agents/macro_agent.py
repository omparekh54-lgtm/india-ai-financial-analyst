from __future__ import annotations

from typing import Any

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim


class IndiaMacroPolicyFlowAgent:
    """India-first macro and flow context with deterministic exposure flags."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        macro = agent_input.context.get("macro") or {}
        if not macro:
            return AgentOutput(
                agent=AgentName.MACRO,
                ok=False,
                warnings=["No macro payload supplied"],
            )

        macro_evidence = [
            item
            for item in agent_input.evidence
            if item.source_type in {"official_macro", "official_flow", "macro", "macro_observation"}
        ]
        evidence_ids = [item.evidence_id for item in macro_evidence]
        metrics = {
            key: macro.get(key)
            for key in (
                "repo_rate",
                "india_10y_yield",
                "usd_inr",
                "usd_inr_change_pct",
                "brent",
                "brent_change_pct",
                "india_vix",
                "cpi_yoy",
                "iip_yoy",
                "fii_cash_net_cr",
                "dii_cash_net_cr",
            )
            if key in macro
        }
        exposure = agent_input.context.get("macro_exposure") or {}
        flags: list[dict[str, object]] = []

        usd_move = _number(macro.get("usd_inr_change_pct"))
        if usd_move is not None and abs(usd_move) >= 1.0:
            flags.append(
                {
                    "factor": "usd_inr",
                    "direction": "INR weakness" if usd_move > 0 else "INR strength",
                    "change_pct": usd_move,
                    "exposure": exposure.get("fx"),
                }
            )

        crude_move = _number(macro.get("brent_change_pct"))
        if crude_move is not None and abs(crude_move) >= 3.0:
            flags.append(
                {
                    "factor": "brent",
                    "direction": "higher crude" if crude_move > 0 else "lower crude",
                    "change_pct": crude_move,
                    "exposure": exposure.get("crude"),
                }
            )

        fii = _number(macro.get("fii_cash_net_cr"))
        if fii is not None and abs(fii) >= 1000:
            flags.append(
                {
                    "factor": "fii_cash_flow",
                    "direction": "net buying" if fii > 0 else "net selling",
                    "value_cr": fii,
                }
            )

        claims = [
            Claim(
                agent=AgentName.MACRO,
                statement=f"Macro factor {flag['factor']} registered a material move",
                claim_type=(
                    "risk"
                    if flag.get("direction") in {"INR weakness", "higher crude", "net selling"}
                    else "catalyst"
                ),
                confidence=0.95 if evidence_ids else 0.45,
                evidence_ids=evidence_ids,
                status="pending",
                data=flag,
            )
            for flag in flags
        ]
        metrics["material_macro_flags"] = flags
        warnings = []
        if flags and not evidence_ids:
            warnings.append("Material macro flags are present but source evidence is unavailable")
        return AgentOutput(
            agent=AgentName.MACRO,
            claims=claims,
            evidence=macro_evidence,
            metrics=metrics,
            warnings=warnings,
        )


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
