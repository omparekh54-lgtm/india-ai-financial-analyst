from __future__ import annotations

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
                claim_type="risk" if flag.get("direction") in {"INR weakness", "higher crude", "net selling"} else "catalyst",
                confidence=0.95,
                status="supported",
                data=flag,
            )
            for flag in flags
        ]
        metrics["material_macro_flags"] = flags
        return AgentOutput(agent=AgentName.MACRO, claims=claims, metrics=metrics)


def _number(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
