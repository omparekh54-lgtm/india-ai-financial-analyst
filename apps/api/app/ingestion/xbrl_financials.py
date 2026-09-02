from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree, html

from app.ingestion.financials import RawFinancialFact

XBRLI_NAMESPACE = "http://www.xbrl.org/2003/instance"
IX_NAMESPACE = "http://www.xbrl.org/2013/inlineXBRL"


@dataclass(frozen=True)
class XbrlContext:
    context_id: str
    period_start: date | None
    period_end: date
    period_type: str


def parse_financial_xbrl(data: bytes, media_type: str) -> list[RawFinancialFact]:
    normalized = media_type.split(";", 1)[0].strip().lower()
    if normalized in {"application/xml", "text/xml", "application/xbrl+xml"}:
        return _parse_instance_xml(data)
    if normalized in {"text/html", "application/xhtml+xml"}:
        return _parse_inline_xbrl(data)
    raise ValueError(f"Unsupported XBRL media type: {normalized}")


def _parse_instance_xml(data: bytes) -> list[RawFinancialFact]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    root = etree.fromstring(data, parser=parser)
    contexts = _xml_contexts(root)
    units = _xml_units(root)
    facts: list[RawFinancialFact] = []

    for element in root.iter():
        context_ref = element.get("contextRef") or element.get("contextref")
        if not context_ref or context_ref not in contexts:
            continue
        if len(element):
            continue
        text_value = "".join(element.itertext()).strip()
        numeric = _numeric_value(text_value, scale=element.get("scale"))
        if numeric is None:
            continue
        context = contexts[context_ref]
        local_name = etree.QName(element.tag).localname
        facts.append(
            RawFinancialFact(
                name=_concept_label(local_name),
                period_start=context.period_start,
                period_end=context.period_end,
                period_type=context.period_type,
                value=numeric,
                unit=units.get(element.get("unitRef") or element.get("unitref") or ""),
                metadata={
                    "source_format": "xbrl",
                    "xbrl_element": local_name,
                    "xbrl_context_id": context_ref,
                    "xbrl_unit_ref": element.get("unitRef") or element.get("unitref"),
                    "xbrl_decimals": element.get("decimals"),
                    "xbrl_scale": element.get("scale"),
                },
            )
        )
    return _dedupe_facts(facts)


def _parse_inline_xbrl(data: bytes) -> list[RawFinancialFact]:
    root = html.fromstring(data)
    contexts = _inline_contexts(root)
    units = _inline_units(root)
    facts: list[RawFinancialFact] = []

    for element in root.iter():
        local_name = _local_name(element.tag)
        if local_name.lower() not in {"nonfraction", "fraction"}:
            continue
        context_ref = _attr(element, "contextref")
        concept = _attr(element, "name")
        if not context_ref or not concept or context_ref not in contexts:
            continue
        text_value = "".join(element.itertext()).strip()
        numeric = _numeric_value(text_value, scale=_attr(element, "scale"))
        if numeric is None:
            continue
        context = contexts[context_ref]
        concept_name = concept.split(":")[-1]
        facts.append(
            RawFinancialFact(
                name=_concept_label(concept_name),
                period_start=context.period_start,
                period_end=context.period_end,
                period_type=context.period_type,
                value=numeric,
                unit=units.get(_attr(element, "unitref") or ""),
                metadata={
                    "source_format": "ixbrl",
                    "xbrl_element": concept,
                    "xbrl_context_id": context_ref,
                    "xbrl_unit_ref": _attr(element, "unitref"),
                    "xbrl_decimals": _attr(element, "decimals"),
                    "xbrl_scale": _attr(element, "scale"),
                },
            )
        )
    return _dedupe_facts(facts)


def _xml_contexts(root: etree._Element) -> dict[str, XbrlContext]:
    output: dict[str, XbrlContext] = {}
    for context in root.xpath("//*[local-name()='context']"):
        context_id = context.get("id")
        if not context_id:
            continue
        period = context.xpath("./*[local-name()='period']")
        if not period:
            continue
        parsed = _period_from_children(period[0])
        if parsed is not None:
            output[context_id] = XbrlContext(context_id=context_id, **parsed)
    return output


def _xml_units(root: etree._Element) -> dict[str, str]:
    output: dict[str, str] = {}
    for unit in root.xpath("//*[local-name()='unit']"):
        unit_id = unit.get("id")
        if not unit_id:
            continue
        measures = ["".join(node.itertext()).strip() for node in unit.xpath(".//*[local-name()='measure']")]
        if measures:
            output[unit_id] = _unit_label(" / ".join(measures))
    return output


def _inline_contexts(root: html.HtmlElement) -> dict[str, XbrlContext]:
    output: dict[str, XbrlContext] = {}
    for context in root.iter():
        if _local_name(context.tag).lower() != "context":
            continue
        context_id = context.get("id")
        if not context_id:
            continue
        period = next(
            (
                child
                for child in context.iterdescendants()
                if _local_name(child.tag).lower() == "period"
            ),
            None,
        )
        if period is None:
            continue
        parsed = _period_from_children(period)
        if parsed is not None:
            output[context_id] = XbrlContext(context_id=context_id, **parsed)
    return output


def _inline_units(root: html.HtmlElement) -> dict[str, str]:
    output: dict[str, str] = {}
    for unit in root.iter():
        if _local_name(unit.tag).lower() != "unit":
            continue
        unit_id = unit.get("id")
        if not unit_id:
            continue
        measures = [
            "".join(child.itertext()).strip()
            for child in unit.iterdescendants()
            if _local_name(child.tag).lower() == "measure"
        ]
        if measures:
            output[unit_id] = _unit_label(" / ".join(measures))
    return output


def _period_from_children(period: etree._Element) -> dict[str, object] | None:
    values = {
        _local_name(child.tag).lower(): "".join(child.itertext()).strip()
        for child in period.iterdescendants()
    }
    instant = _parse_date(values.get("instant"))
    if instant is not None:
        return {
            "period_start": None,
            "period_end": instant,
            "period_type": "point_in_time",
        }

    start = _parse_date(values.get("startdate"))
    end = _parse_date(values.get("enddate"))
    if start is None or end is None:
        return None
    return {
        "period_start": start,
        "period_end": end,
        "period_type": _duration_period_type(start, end),
    }


def _duration_period_type(start: date, end: date) -> str:
    days = (end - start).days + 1
    if days <= 105:
        return "quarterly"
    if days <= 200:
        return "half_year"
    if days <= 300:
        return "nine_month"
    if days <= 390:
        return "annual"
    return "duration"


def _numeric_value(value: str, *, scale: str | None) -> Decimal | None:
    cleaned = value.strip().replace(",", "").replace("₹", "")
    if cleaned in {"", "-", "--", "NA", "N/A", "Nil", "nil"}:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    cleaned = re.sub(r"\s+", "", cleaned)
    try:
        result = Decimal(cleaned)
    except InvalidOperation:
        return None
    if negative:
        result = -result
    if scale:
        try:
            result *= Decimal(10) ** int(scale)
        except (InvalidOperation, ValueError):
            return None
    return result if result.is_finite() else None


def _concept_label(value: str) -> str:
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _unit_label(value: str) -> str:
    lowered = value.lower()
    if "inr" in lowered:
        return "INR"
    if "shares" in lowered or "share" in lowered:
        return "shares"
    if "pure" in lowered:
        return "ratio"
    return value


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _attr(element: etree._Element, name: str) -> str | None:
    lowered = name.lower()
    for key, value in element.attrib.items():
        if _local_name(key).lower() == lowered:
            return value
    return None


def _local_name(tag: object) -> str:
    value = str(tag)
    if value.startswith("{") and "}" in value:
        return value.split("}", 1)[1]
    return value.split(":")[-1]


def _dedupe_facts(facts: list[RawFinancialFact]) -> list[RawFinancialFact]:
    seen: set[tuple[str, date, str, Decimal]] = set()
    output: list[RawFinancialFact] = []
    for fact in facts:
        key = (fact.name.lower(), fact.period_end, fact.period_type, Decimal(str(fact.value)))
        if key in seen:
            continue
        seen.add(key)
        output.append(fact)
    return output
