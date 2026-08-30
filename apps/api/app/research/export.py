from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any


def research_export_payload(job: Mapping[str, object]) -> dict[str, object]:
    """Return an ownership-scoped export payload without adding or recalculating research data."""
    report = _mapping(job.get("report_json"))
    return {
        "job_id": _text(job.get("id")),
        "query": _text(job.get("query")),
        "mode": _text(job.get("mode")),
        "status": _text(job.get("status")),
        "security": {
            "security_id": _text(job.get("security_id")) or None,
            "legal_name": _text(job.get("legal_name")) or None,
            "nse_symbol": _text(job.get("nse_symbol")) or None,
            "bse_code": _text(job.get("bse_code")) or None,
        },
        "created_at": _json_scalar(job.get("created_at")),
        "completed_at": _json_scalar(job.get("completed_at")),
        "confidence": {
            "data_confidence": _json_scalar(job.get("data_confidence")),
            "thesis_confidence": _json_scalar(job.get("thesis_confidence")),
            "valuation_confidence": _json_scalar(job.get("valuation_confidence")),
            "catalyst_confidence": _json_scalar(job.get("catalyst_confidence")),
        },
        "report": dict(report),
    }


def render_research_markdown(job: Mapping[str, object]) -> str:
    """Render only the persisted report and its existing evidence references as Markdown."""
    report = _mapping(job.get("report_json"))
    if not report:
        raise ValueError("research job does not have a persisted report")

    security = _mapping(report.get("security"))
    name = (
        _text(security.get("legal_name"))
        or _text(job.get("legal_name"))
        or _text(job.get("query"))
        or "Research report"
    )
    symbol = _text(security.get("nse_symbol")) or _text(job.get("nse_symbol"))
    mode = _text(report.get("mode")) or _text(job.get("mode"))
    job_id = _text(job.get("id"))

    lines = [f"# {_single_line(name)}", ""]
    metadata = []
    if symbol:
        metadata.append(f"NSE: {_single_line(symbol)}")
    if mode:
        metadata.append(f"Mode: {_single_line(mode)}")
    if job_id:
        metadata.append(f"Job: {_single_line(job_id)}")
    if metadata:
        lines.extend([" · ".join(metadata), ""])

    summary = _text(report.get("executive_summary"))
    if summary:
        lines.extend(["## Executive summary", "", summary, ""])

    narrative = _mapping(report.get("narrative"))
    _append_string_list(lines, "Bull case", narrative.get("bull_case"))
    _append_string_list(lines, "Bear case", narrative.get("bear_case"))
    _append_string_list(lines, "Watch items", narrative.get("watch_items"))
    confidence_note = _text(narrative.get("confidence_note"))
    if confidence_note:
        lines.extend(["## Confidence note", "", confidence_note, ""])

    confidence = _mapping(report.get("confidence"))
    if confidence:
        lines.extend(["## Confidence", ""])
        for key in (
            "data_confidence",
            "thesis_confidence",
            "valuation_confidence",
            "catalyst_confidence",
        ):
            value = confidence.get(key)
            rendered = _confidence(value)
            if rendered:
                lines.append(f"- {key.replace('_', ' ').title()}: {rendered}")
        lines.append("")

    evidence_catalog = _mapping(report.get("evidence_catalog"))
    sections = _mapping(report.get("sections"))
    for section_name, raw_claims in sections.items():
        claims = raw_claims if isinstance(raw_claims, list) else []
        if not claims:
            continue
        lines.extend([f"## {_single_line(str(section_name).replace('_', ' ').title())}", ""])
        for raw_claim in claims:
            claim = _mapping(raw_claim)
            statement = _text(claim.get("statement"))
            if not statement:
                continue
            status = _text(claim.get("status")) or "unknown"
            confidence_text = _confidence(claim.get("confidence"))
            qualifier = status.upper()
            if confidence_text:
                qualifier += f" · {confidence_text}"
            lines.append(f"- **{_single_line(qualifier)}** — {statement}")
            for evidence_id in _strings(claim.get("evidence_ids")):
                evidence = _mapping(evidence_catalog.get(evidence_id))
                if not evidence:
                    continue
                title = _text(evidence.get("title")) or _text(evidence.get("source_type"))
                source_uri = _text(evidence.get("source_uri"))
                published_at = _text(evidence.get("published_at"))
                detail_parts = [part for part in (title, published_at) if part]
                detail = " · ".join(_single_line(part) for part in detail_parts)
                if source_uri:
                    detail = f"{detail} — <{_single_line(source_uri)}>" if detail else (
                        f"<{_single_line(source_uri)}>"
                    )
                if detail:
                    lines.append(f"  - Evidence: {detail}")
        lines.append("")

    warnings = _strings(report.get("warnings"))
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {_single_line(item)}" for item in warnings)
        lines.append("")

    validation = _mapping(report.get("validation"))
    if validation:
        coverage = _confidence(validation.get("evidence_coverage"))
        if coverage:
            lines.extend(["## Validation", "", f"- Evidence coverage: {coverage}", ""])

    disclaimer = _text(report.get("research_disclaimer"))
    if disclaimer:
        lines.extend(["---", "", disclaimer, ""])

    return "\n".join(lines).strip() + "\n"


def _append_string_list(lines: list[str], heading: str, value: object) -> None:
    items = _strings(value)
    if not items:
        return
    lines.extend([f"## {heading}", ""])
    lines.extend(f"- {_single_line(item)}" for item in items)
    lines.append("")


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item))]


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _single_line(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def _confidence(value: object) -> str:
    if not isinstance(value, int | float):
        return ""
    if value < 0 or value > 1:
        return ""
    return f"{round(float(value) * 100)}%"


def _json_scalar(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
