import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.connectors.http_fetcher import SourceFetchError
from scripts.recover_lt_financial_documents import CurlLtXbrlFetcher, validate_lt_issuer


@pytest.mark.asyncio
async def test_archive_transport_does_not_require_nse_website_session(monkeypatch) -> None:
    client = AsyncMock()
    monkeypatch.setattr(
        "scripts.recover_lt_financial_documents.httpx.AsyncClient", lambda **_kwargs: client,
    )
    fetcher = CurlLtXbrlFetcher()
    fetcher._refresh_session = AsyncMock(side_effect=SourceFetchError("Website unavailable"))
    async with fetcher:
        assert fetcher._client is not None
    fetcher._refresh_session.assert_not_awaited()
    client.aclose.assert_awaited_once()
    assert fetcher._client is None


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


@pytest.mark.parametrize("entrypoint", [
    ["scripts/recover_lt_financial_documents.py"],
    ["-m", "scripts.recover_lt_financial_documents"],
])
def test_recovery_cli_imports_before_database_access(entrypoint) -> None:
    result = subprocess.run(
        [sys.executable, *entrypoint, "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--refresh-readiness-only" in result.stdout
