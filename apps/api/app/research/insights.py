from __future__ import annotations

import re
from typing import Any

from app.agents.contracts import AgentName


def special_mode_insights(
    mode: str,
    context: dict[str, Any],
    grouped_claims: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    if mode == "what_changed":
        return what_changed(context.get("previous_snapshot"), grouped_claims)
    if mode == "why_did_it_move":
        return why_did_it_move(context, grouped_claims)
    return None


def what_changed(
    previous_snapshot: object,
    grouped_claims: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    current_risks = grouped_claims.get(AgentName.RISK.value, [])
    current_catalysts = [
        claim
        for section in (AgentName.NEWS.value, AgentName.EARNINGS.value)
        for claim in grouped_claims.get(section, [])
        if claim.get("claim_type") == "catalyst"
    ]

    if not isinstance(previous_snapshot, dict):
        return {
            "baseline_available": False,
            "new_risks": current_risks,
            "new_catalysts": current_catalysts,
            "resolved_risks": [],
            "resolved_catalysts": [],
            "note": "No prior analysis snapshot exists; current material items are shown as the baseline.",
        }

    previous_risks = _claim_list(previous_snapshot.get("risks"))
    previous_catalysts = _claim_list(previous_snapshot.get("catalysts"))
    return {
        "baseline_available": True,
        "baseline_at": previous_snapshot.get("snapshot_at"),
        "new_risks": _new_items(current_risks, previous_risks),
        "resolved_risks": _new_items(previous_risks, current_risks),
        "new_catalysts": _new_items(current_catalysts, previous_catalysts),
        "resolved_catalysts": _new_items(previous_catalysts, current_catalysts),
    }


def why_did_it_move(
    context: dict[str, Any],
    grouped_claims: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    market = context.get("market_metrics") if isinstance(context.get("market_metrics"), dict) else {}
    macro = context.get("macro_metrics") if isinstance(context.get("macro_metrics"), dict) else {}
    drivers: list[dict[str, Any]] = []

    relative = _number(market.get("relative_to_sector_pct")) or _number(
        market.get("relative_to_benchmark_pct")
    )
    if relative is not None and abs(relative) >= 0.75:
        drivers.append(
            {
                "type": "stock_specific_relative_move",
                "score": min(1.0, abs(relative) / 5.0),
                "direction": "positive" if relative > 0 else "negative",
                "detail": f"Stock moved {relative:.2f} percentage points versus its comparison benchmark.",
            }
        )

    for claim in grouped_claims.get(AgentName.NEWS.value, []):
        materiality = str((claim.get("data") or {}).get("materiality") or "low")
        if materiality not in {"high", "medium"}:
            continue
        drivers.append(
            {
                "type": "company_event",
                "score": 0.9 if materiality == "high" else 0.65,
                "direction": _claim_direction(claim),
                "detail": claim.get("statement"),
                "evidence_ids": claim.get("evidence_ids", []),
            }
        )

    for flag in macro.get("material_macro_flags", []) if isinstance(macro, dict) else []:
        if not isinstance(flag, dict):
            continue
        drivers.append(
            {
                "type": "macro_or_flow",
                "score": 0.55,
                "direction": _flag_direction(flag),
                "detail": flag,
            }
        )

    drivers.sort(key=lambda item: float(item.get("score", 0)), reverse=True)
    return {
        "candidate_drivers": drivers[:8],
        "causality_status": "candidate_explanation_not_proven_causality",
        "note": (
            "Drivers are ranked evidence-based candidates. Market moves can have multiple causes, "
            "so the system does not present correlation as proven causation."
        ),
    }


def _claim_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _new_items(
    candidates: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_keys = {_claim_key(item) for item in baseline}
    return [item for item in candidates if _claim_key(item) not in baseline_keys]


def _claim_key(claim: dict[str, Any]) -> str:
    statement = str(claim.get("statement") or "")
    return re.sub(r"[^a-z0-9]+", " ", statement.lower()).strip()


def _claim_direction(claim: dict[str, Any]) -> str:
    claim_type = claim.get("claim_type")
    if claim_type == "risk":
        return "negative"
    if claim_type == "catalyst":
        return "positive_or_context_dependent"
    return "context_dependent"


def _flag_direction(flag: dict[str, Any]) -> str:
    direction = str(flag.get("direction") or "").lower()
    if direction in {"inr weakness", "higher crude", "net selling"}:
        return "negative_for_exposed_companies"
    if direction in {"inr strength", "lower crude", "net buying"}:
        return "positive_for_exposed_companies"
    return "context_dependent"


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
