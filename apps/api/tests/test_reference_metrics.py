from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.ingestion.metrics import normalize_security_metric
from app.ingestion.reference_files import ReferenceFileError
from app.ingestion.reference_metrics import parse_security_metrics_csv


def test_security_metrics_csv_parses_canonical_metrics() -> None:
    content = """metric_name,as_of_date,value,unit,metadata_json
Price to Earnings,2026-08-29,24.5,x,"{""source_scope"":""ttm""}"
Return on Capital Employed,2026-08-29,18.2,%,
Market Capitalization,2026-08-29,"2,000,000",INR cr,
"""
    metrics = parse_security_metrics_csv(content, min_rows=3)
    normalized = [normalize_security_metric(metric) for metric in metrics]
    by_name = {metric.metric_name: metric for metric in normalized}

    assert by_name["pe"].as_of_date == date(2026, 8, 29)
    assert by_name["pe"].value == Decimal("24.5")
    assert by_name["pe"].metadata["source_scope"] == "ttm"
    assert by_name["roce"].value == Decimal("18.2")
    assert by_name["market_cap"].value == Decimal(2000000)


def test_security_metrics_csv_rejects_alias_duplicates() -> None:
    content = """metric_name,as_of_date,value
PE,2026-08-29,20
Price to Earnings,2026-08-29,20
"""
    with pytest.raises(ReferenceFileError, match="duplicate canonical security metric"):
        parse_security_metrics_csv(content)


def test_security_metrics_csv_rejects_non_object_metadata() -> None:
    content = """metric_name,as_of_date,value,metadata_json
PE,2026-08-29,20,"[1,2]"
"""
    with pytest.raises(ReferenceFileError, match="must be a JSON object"):
        parse_security_metrics_csv(content)


def test_security_metrics_csv_rejects_missing_required_fields() -> None:
    content = """metric_name,as_of_date,value
PE,2026-08-29,
"""
    with pytest.raises(ReferenceFileError, match="are required"):
        parse_security_metrics_csv(content)


def test_security_metrics_csv_enforces_minimum_rows() -> None:
    content = """metric_name,as_of_date,value
PE,2026-08-29,20
"""
    with pytest.raises(ReferenceFileError, match="minimum expected is 2"):
        parse_security_metrics_csv(content, min_rows=2)
