import pytest

from app.connectors.nse_sectoral_indices import (
    NSE_NIFTY50_INDEX_CSV,
    NseSectoralIndexFetcher,
    parse_sectoral_index_csv,
)


def test_official_nifty50_constituent_csv_is_parsed_without_hard_coding_symbols() -> None:
    entries = parse_sectoral_index_csv(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\n"
        "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"
    )

    assert [entry.symbol for entry in entries] == ["RELIANCE", "HDFCBANK"]
    assert entries[0].isin == "INE002A01018"


def test_index_fetcher_accepts_only_official_nse_archive_urls() -> None:
    fetcher = NseSectoralIndexFetcher(
        source_url=NSE_NIFTY50_INDEX_CSV,
        index_name="NIFTY 50",
    )
    assert fetcher.source_url == NSE_NIFTY50_INDEX_CSV

    with pytest.raises(ValueError, match="official NSE indices archive"):
        NseSectoralIndexFetcher(source_url="https://example.com/index.csv")
