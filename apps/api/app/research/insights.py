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
    empty_deltas: dict[str, list[dict[str, Any]]] = {
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
            "Structured event/metric identities are used before statement text so wording changes "
            "do not create false new or resolved items."
        ),
    }


def why_did_it_move(
    context: dict[str, Any],
    grouped_claims: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    market = _mapping(context.get("market_metrics"))
    macro = _mapping(context.get("macro_metrics"))
    technical = _mapping(context.get("technical_metrics"))
    derivatives = _mapping(technical.get("derivatives"))
    drivers: list[dict[str, Any]] = []

    stock_move = _number(market.get("change_pct"))
    benchmark_move = _number(market.get("benchmark_change_pct"))
    sector_move = _number(market.get("sector_change_pct"))
    relative_sector = _number(market.get("relative_to_sector_pct"))
    relative_benchmark = _number(market.get("relative_to_benchmark_pct"))
    relative = _first_number(relative_sector, relative_benchmark)
    volume_ratio = _number(market.get("volume_ratio"))

    if stock_move is not None and abs(stock_move) >= 1.0:
        drivers.append(
            {
                "type": "absolute_stock_move",
                "score": min(0.70, 0.20 + abs(stock_move) / 10.0),
                "direction": "positive" if stock_move > 0 else "negative",
                "detail": f"Stock moved {stock_move:.2f}% versus the previous close.",
            }
        )

    if relative is not None and abs(relative) >= 0.75:
        drivers.append(
            {
                "type": "stock_specific_relative_move",
                "score": min(0.80, 0.35 + abs(relative) / 8.0),
                "direction": "positive" if relative > 0 else "negative",
                "detail": (
                    f"Stock moved {relative:.2f} percentage points versus its closest "
                    "comparison benchmark, increasing the likelihood of company-specific factors."
                ),
            }
        )

    if (
        stock_move is not None
        and benchmark_move is not None
        and abs(benchmark_move) >= 0.75
        and _same_direction(stock_move, benchmark_move)
    ):
        drivers.append(
            {
                "type": "broad_market_factor",
                "score": min(0.60, 0.30 + abs(benchmark_move) / 8.0),
                "direction": "positive" if benchmark_move > 0 else "negative",
                "detail": (
                    f"Broad benchmark moved {benchmark_move:.2f}% in the same direction as the stock."
                ),
            }
        )

    if (
        stock_move is not None
        and sector_move is not None
        and abs(sector_move) >= 0.75
        and _same_direction(stock_move, sector_move)
        and (relative_sector is None or abs(relative_sector) < max(1.0, abs(sector_move)))
    ):
        drivers.append(
            {
                "type": "sector_factor",
                "score": min(0.65, 0.35 + abs(sector_move) / 8.0),
                "direction": "positive" if sector_move > 0 else "negative",
                "detail": (
                    f"Sector benchmark moved {sector_move:.2f}% in the same direction, suggesting "
                    "part of the move may be sector-wide rather than company-specific."
                ),
            }
        )

    if volume_ratio is not None and volume_ratio >= 1.5:
        drivers.append(
            {
                "type": "volume_confirmation",
                "score": min(0.58, 0.30 + (volume_ratio - 1.0) / 5.0),
                "direction": "confirms_move_not_direction",
                "detail": f"Trading volume was {volume_ratio:.2f}x its recent average.",
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

    rsi_value = _number(technical.get("rsi_14"))
    if rsi_value is not None and (rsi_value <= 30 or rsi_value >= 70):
        drivers.append(
            {
                "type": "technical_momentum_condition",
                "score": 0.3,
                "direction": "context_dependent",
                "detail": f"RSI(14) is {rsi_value:.1f}, indicating an extreme momentum condition.",
            }
        )

    realized_vol = _number(technical.get("realized_volatility_20d"))
    if realized_vol is not None and realized_vol >= 0.45:
        drivers.append(
            {
                "type": "elevated_realized_volatility",
                "score": 0.35,
                "direction": "context_dependent",
                "detail": f"20-day realized volatility is elevated at {realized_vol:.2%}.",
            }
        )

    basis = _number(derivatives.get("futures_basis_pct"))
    if basis is not None and abs(basis) >= 0.5:
        drivers.append(
            {
                "type": "futures_basis_context",
                "score": min(0.5, 0.25 + abs(basis) / 10.0),
                "direction": "positive" if basis > 0 else "negative",
                "detail": f"Near futures basis is {basis:.2f}% versus spot.",
            }
        )

    oi_change = _number(derivatives.get("futures_oi_change_pct"))
    if oi_change is not None and abs(oi_change) >= 10:
        drivers.append(
            {
                "type": "futures_open_interest_change",
                "score": min(0.45, 0.25 + abs(oi_change) / 100.0),
                "direction": "context_dependent",
                "detail": f"Futures open interest changed {oi_change:.2f}% from the prior snapshot.",
            }
        )

    pcr = _number(derivatives.get("put_call_oi_ratio"))
    if pcr is not None and (pcr <= 0.7 or pcr >= 1.5):
        drivers.append(
            {
                "type": "options_positioning_context",
                "score": 0.3,
                "direction": "context_dependent",
                "detail": f"Put/call open-interest ratio is {pcr:.2f}, an extreme positioning reading.",
            }
        )

    drivers.sort(key=lambda item: _number(item.get("score")) or 0.0, reverse=True)
    return {
        "candidate_drivers": drivers[:10],
        "causality_status": "candidate_explanation_not_proven_causality",
        "market_move_pct": stock_move,
        "benchmark_move_pct": benchmark_move,
        "sector_move_pct": sector_move,
        "volume_ratio": volume_ratio,
        "note": (
            "Drivers are ranked evidence-based candidates. Market, sector, volume, technical and "
            "derivatives conditions are context, not proof of cause; market moves can have multiple causes."
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
    agent = str(claim.get("agent") or "")
    claim_type = str(claim.get("claim_type") or "")
    metric = str(claim.get("metric") or "").strip().lower()
    data = _mapping(claim.get("data"))
    event_type = str(data.get("event_type") or "").strip().lower()
    title = str(data.get("title") or "").strip().lower()
    source_uri = str(data.get("source_uri") or "").strip().lower()

    if event_type:
        stable_event = "|".join(part for part in (agent, claim_type, event_type, source_uri) if part)
        return f"event:{stable_event}"
    if metric:
        return f"metric:{agent}|{claim_type}|{metric}"
    if title:
        normalized_title = re.sub(r"[^a-z0-9]+", " ", title).strip()
        return f"title:{agent}|{claim_type}|{normalized_title}"

    statement = str(claim.get("statement") or "")
    normalized_statement = re.sub(r"[^a-z0-9]+", " ", statement.lower()).strip()
    return f"statement:{agent}|{claim_type}|{normalized_statement}"


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
        key=lambda item: abs(
            _number(item.get("pct_change")) or _number(item["absolute_change"]) or 0.0
        ),
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


def _same_direction(left: float, right: float) -> bool:
    return (left > 0 and right > 0) or (left < 0 and right < 0)


def _first_number(*values: object) -> float | None:
    for value in values:
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return None


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
