from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.derived_metrics import MetricMarketClose, derive_peer_metrics
from scripts import complete_remaining_peer_metrics as recovery
from tests.test_derived_metrics import S1, S2, _filed, _metrics


def test_actual_reviewed_share_inputs_reconcile() -> None:
    facts = recovery.reviewed_shares()
    assert facts["HDFCLIFE"].value == Decimal(2157819536)
    assert facts["SBILIFE"].value == Decimal(1003092100)
    assert all(f.period_end == date(2026, 3, 31) and f.unit == "shares" for f in facts.values())
    assert facts["SBILIFE"].metadata["reported_capital_decimals"] == "-3"


def test_reviewed_share_recovery_rejects_changed_excerpt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for filename in ("hdfclife-page166.txt", "sbilife-original.xml"):
        (tmp_path / filename).write_bytes((recovery.DIRECTORY / filename).read_bytes())
    (tmp_path / "hdfclife-page166.txt").write_text("changed capital and shares")
    monkeypatch.setattr(recovery, "DIRECTORY", tmp_path)
    with pytest.raises(ValueError, match="checksum mismatch"):
        recovery.reviewed_shares()


def test_insurer_book_value_uses_shareholders_funds() -> None:
    from app.ingestion.derived_metrics import MetricFinancialFact

    end = date(2026, 3, 31)
    facts = [_filed("shareholders_funds", "ShareholdersFunds", "100", end, period_type="point_in_time"),
             _filed("policyholders_funds", "PolicyholdersFunds", "900", end, period_type="point_in_time"),
             MetricFinancialFact("shares_outstanding", end, "point_in_time", Decimal(5), "shares", S1)]
    metric = _metrics(derive_peer_metrics(facts, market=MetricMarketClose(date(2026, 9, 30), Decimal(40), S2)))["pb"]
    assert metric.value == 2
    facts[0] = _filed("shareholders_funds", "PolicyholdersFunds", "100", end, period_type="point_in_time")
    assert "pb" not in _metrics(derive_peer_metrics(facts, market=MetricMarketClose(date(2026, 9, 30), Decimal(40), S2)))
