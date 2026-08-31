from __future__ import annotations

import re
from typing import Any

from app.agents.contracts import AgentName


def special_mode_insights(
    mode: str,
    context: dict[str, Any],
    grouped_claims: dict[str, list[dict[str, Any]]],
    *,
    current_confidence: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    if mode == "what_changed":
        return what_changed(
            context.get("previous_snapshot"),
            grouped_claims,
            context=context,
            current_confidence=current_confidence,
        )
    if mode == "why_did_it_move":
        return why_did_it_move(context, grouped_claims)
    return None


def what_changed(
    previous_snapshot: object,
    grouped_claims: dict[str, list[dict[str, Any]]],
    *,
    context: dict[str, Any] | None = None,
    current_confidence: dict[str, float] | None = None,
) -> dict[str, Any]:
    current_risks = grouped_claims.get(AgentName.RISK.value, [])
    current_catalysts = [
        claim
        for section in (AgentName.NEWS.value, AgentName.EARNINGS.value)
        for claim in grouped_claims.get(section, [])
        if claim.get("claim_type") == "catalyst"
    ]
    current_disclosures = [
        claim
        for section in (AgentName.FILINGS.value, AgentName.EARNINGS.value)
        for claim in grouped_claims.get(section, [])
    ]

    current_context = context or {}
    empty_deltas = {
        "market_changes": [],
        "financial_changes": [],
        "valuation_changes": [],
        "confidence_changes": [],
    }
    if not isinstance(previous_snapshot, dict):
        return {
            "baseline_available": False,
            "new_risks": current_risks,
            "new_catalysts": current_catalysts,
            "new_disclosures": current_disclosures,
            "resolved_risks": [],
            "resolved_catalysts": [],
            **empty_deltas,
            "note": "No prior validated snapshot exists; current material items are shown as the baseline.",
        }

    previous_risks = _claim_list(previous_snapshot.get("risks"))
    previous_catalysts = _claim_list(previous_snapshot.get("catalysts"))
    previous_metadata = _mapping(previous_snapshot.get("metadata"))
    previous_disclosures = _claim_list(previous_metadata.get("disclosure_claims"))
    previous_metrics = _mapping(previous_snapshot.get("metrics"))
    previous_confidence = _previous_confidence(previous_metrics)

    return {
        "baseline_available": True,
        "baseline_at": previous_snapshot.get("snapshot_at"),
        "new_risks": _new_items(current_risks, previous_risks),
        "resolved_risks": _new_items(previous_risks, current_risks),
        "new_catalysts": _new_items(current_catalysts, previous_catalysts),
        "resolved_catalysts": _new_items(previous_catalysts, current_catalysts),
        "new_disclosures": _new_items(current_disclosures, previous_disclosures),
        "market_changes": _metric_changes(
            _mapping(previous_metrics.get("market")),
            _mapping(current_context.get("market_metrics")),
        ),
        "financial_changes": _metric_changes(
            _mapping(previous_metrics.get("financials")),
            _mapping(current_context.get("financial_metrics")),
        ),
        "valuation_changes": _metric_changes(
            _mapping(previous_metrics.get("valuation")),
            _mapping(current_context.get("valuation_metrics")),
        ),
        "confidence_changes": _metric_changes(
            previous_confidence,
            current_confidence or {},
            limit=8,
        ),
        "note": (
            "Changes compare the current validated run with the latest prior validated snapshot. "
            "Numeric deltas are computed deterministically from persisted specialist metrics."
        ),
    }


def why_did_it_move(
    context: dict[str, Any],
    grouped_claims: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    market_value = context.get("market_metrics")
    macro_value = context.get("macro_metrics")
    market: dict[str, Any] = market_value if isinstance(market_value, dict) else {}
    macro: dict[str, Any] = macro_value if isinstance(macro_value, dict) else {}
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
        claim_data = claim.get("data")
        data = claim_data if isinstance(claim_data, dict) else {}
        materiality = str(data.get("materiality") or "low")
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

    for flag in macro.get("material_macro_flags", []):
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

    drivers.sort(key=lambda item: _number(item.get("score")) or 0.0, reverse=True)
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


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _previous_confidence(metrics: dict[str, Any]) -> dict[str, Any]:
    nested = metrics.get("confidence")
    if isinstance(nested, dict):
        return nested
    if any(str(key).endswith("_confidence") for key in metrics):
        return metrics
    return {}


def _metric_changes(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    previous_numeric = _flatten_numeric(previous)
    current_numeric = _flatten_numeric(current)
    changes: list[dict[str, Any]] = []
    for metric in sorted(previous_numeric.keys() & current_numeric.keys()):
        before = previous_numeric[metric]
        after = current_numeric[metric]
        absolute_change = after - before
        if abs(absolute_change) <= 1e-12:
            continue
        pct_change = None if before == 0 else (absolute_change / abs(before)) * 100.0
        changes.append(
            {
                "metric": metric,
                "previous": before,
                "current": after,
                "absolute_change": absolute_change,
                "pct_change": pct_change,
            }
        )
    changes.sort(
        key=lambda item: abs(_number(item.get("pct_change")) or _number(item["absolute_change"]) or 0.0),
        reverse=True,
    )
    return changes[:limit]


def _flatten_numeric(
    value: dict[str, Any],
    *,
    prefix: str = "",
    depth: int = 0,
) -> dict[str, float]:
    output: dict[str, float] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        number = _number(item)
        if number is not None and not isinstance(item, bool):
            output[path] = number
        elif isinstance(item, dict) and depth < 2:
            output.update(_flatten_numeric(item, prefix=path, depth=depth + 1))
    return output


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


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
