from app.orchestration.plan import EventTrigger


_GOVERNANCE_EVENTS = {
    "auditor_resignation",
    "auditor_qualification",
    "cfo_change",
    "ceo_change",
    "director_change",
    "promoter_pledge",
    "promoter_transaction",
    "shareholding_pattern",
    "related_party",
    "credit_rating",
    "regulatory_action",
    "sebi_action",
    "exchange_penalty",
    "litigation",
    "tax_notice",
    "preferential_issue",
    "qip",
    "rights_issue",
}

_FINANCIAL_RESULT_EVENTS = {
    "financial_results",
    "quarterly_results",
    "quarterly_result",
    "earnings_release",
    "earnings_call",
    "earnings_transcript",
    "investor_presentation",
}


def classify_corporate_event(event_type: str) -> EventTrigger | None:
    """Map a normalized exchange event to the smallest safe v2 research trigger."""
    normalized = event_type.strip().lower()
    if normalized == "annual_report":
        return EventTrigger.ANNUAL_REPORT
    if normalized in _FINANCIAL_RESULT_EVENTS:
        return EventTrigger.QUARTERLY_RESULT
    if normalized in _GOVERNANCE_EVENTS:
        return EventTrigger.GOVERNANCE_FILING
    return None
