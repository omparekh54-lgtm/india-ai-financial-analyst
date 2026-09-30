import pytest

from app.connectors.http_fetcher import SourceFetchError
from scripts.recover_lt_financial_documents import validate_lt_issuer


def test_recovery_accepts_only_verified_lt_issuer() -> None:
    validate_lt_issuer(b"<xbrl><ISIN>INE018A01030</ISIN></xbrl>")


@pytest.mark.parametrize("content", [
    b"<xbrl><ISIN>INE002A01018</ISIN></xbrl>",
    b"<xbrl><Symbol>LT</Symbol></xbrl>",
    b"<xbrl><ISIN>INE018A01030</ISIN><ISIN>INE002A01018</ISIN></xbrl>",
])
def test_recovery_rejects_wrong_missing_or_ambiguous_issuer(content) -> None:
    with pytest.raises(SourceFetchError, match="verified LT ISIN"):
        validate_lt_issuer(content)
