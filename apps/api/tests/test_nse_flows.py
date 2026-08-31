from datetime import date

import pytest

from app.connectors.nse_flows import parse_nse_fii_dii_cash_flows


def test_parse_nse_fii_dii_cash_flows() -> None:
    observations = parse_nse_fii_dii_cash_flows(
        [
            {
                "category": "FII/FPI",
                "date": "21-Aug-2026",
                "buyValue": "12,560.91",
                "sellValue": "13,103.62",
                "netValue": "-542.71",
            },
            {
                "category": "DII",
                "date": "21-Aug-2026",
                "buyValue": 15258.71,
                "sellValue": 13134.57,
                "netValue": 2124.14,
            },
        ]
    )

    assert [item.series_key for item in observations] == [
        "fii_cash_net_cr",
        "dii_cash_net_cr",
    ]
    assert observations[0].observation_date == date(2026, 8, 21)
    assert observations[0].value == -542.71
    assert observations[1].value == 2124.14
    assert observations[0].unit == "INR cr"
    assert observations[0].metadata["provisional"] is True


def test_parse_nse_fii_dii_cash_flows_accepts_parenthesized_negative() -> None:
    observations = parse_nse_fii_dii_cash_flows(
        [
            {
                "category": "FII/FPI",
                "date": "21-Aug-2026",
                "buyValue": "100",
                "sellValue": "150",
                "netValue": "(50)",
            },
            {
                "category": "DII",
                "date": "21-Aug-2026",
                "buyValue": "175",
                "sellValue": "100",
                "netValue": "75",
            },
        ]
    )

    assert observations[0].value == -50.0


def test_parse_nse_fii_dii_cash_flows_requires_both_categories() -> None:
    with pytest.raises(ValueError, match="missing required flow series"):
        parse_nse_fii_dii_cash_flows(
            [
                {
                    "category": "FII/FPI",
                    "date": "21-Aug-2026",
                    "buyValue": 100,
                    "sellValue": 90,
                    "netValue": 10,
                }
            ]
        )


def test_parse_nse_fii_dii_cash_flows_rejects_mismatched_dates() -> None:
    with pytest.raises(ValueError, match="same reporting date"):
        parse_nse_fii_dii_cash_flows(
            [
                {
                    "category": "FII/FPI",
                    "date": "20-Aug-2026",
                    "buyValue": 100,
                    "sellValue": 90,
                    "netValue": 10,
                },
                {
                    "category": "DII",
                    "date": "21-Aug-2026",
                    "buyValue": 90,
                    "sellValue": 100,
                    "netValue": -10,
                },
            ]
        )


def test_parse_nse_fii_dii_cash_flows_reconciles_net_value() -> None:
    with pytest.raises(ValueError, match="does not reconcile"):
        parse_nse_fii_dii_cash_flows(
            [
                {
                    "category": "FII/FPI",
                    "date": "21-Aug-2026",
                    "buyValue": 100,
                    "sellValue": 90,
                    "netValue": 50,
                },
                {
                    "category": "DII",
                    "date": "21-Aug-2026",
                    "buyValue": 90,
                    "sellValue": 100,
                    "netValue": -10,
                },
            ]
        )
