import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.financial_history_policy import evaluate_financial_history
from scripts.backfill_nse_financial_results import FinancialTarget, _process_target


def _validate(*, available: bool, minimum: int = 0, count: int = 6):
    selected = [SimpleNamespace(
        record=SimpleNamespace(period_end=date(2026, 6, 30), period="Quarterly",
                               xbrl_url="https://nsearchives.nseindia.com/example.xml"),
        timestamp_basis="nse_filing_at",
    ) for _ in range(count)]
    with patch("scripts.backfill_nse_financial_results.select_financial_result_records",
               return_value=selected):
        return asyncio.run(_process_target(
            engine=MagicMock(),
            target=FinancialTarget(uuid4(), "TEST", "Test", date(2010, 1, 1)),
            results_fetcher=SimpleNamespace(fetch_history=AsyncMock(return_value=[])),
            xbrl_fetcher=MagicMock(), max_periods=10, min_selected_periods=minimum,
            document_delay_seconds=0, dry_run=True, collect_available_history=available,
        ))


def test_available_filings_can_be_collected_without_claiming_readiness() -> None:
    result = _validate(available=True)
    assert result["available_document_count"] == 6
    assert result["document_history_gap"] == 2
    history = evaluate_financial_history(
        listing_date=date(2010, 1, 1), as_of=date(2026, 9, 30),
        period_count=6, fact_type_count=6, latest_period_end=date(2026, 6, 30),
    )
    assert history.complete is False


@pytest.mark.parametrize("available,minimum,count", [(False, 0, 6), (True, 8, 6), (True, 0, 0)])
def test_strict_minimum_and_missing_documents_still_fail(available, minimum, count) -> None:
    with pytest.raises(ValueError, match="minimum required"):
        _validate(available=available, minimum=minimum, count=count)
