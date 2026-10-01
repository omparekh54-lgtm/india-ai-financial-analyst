from datetime import date
from pathlib import Path

import httpx
import pytest

from scripts import collect_official_peer_inputs as collector


def test_reviewed_inputs_verify_real_download_checksums() -> None:
    content, identities = collector.reviewed_inputs(date(2026, 9, 30), "full-delivery")
    assert len(content) == 399899
    assert len(identities) == 3344


@pytest.mark.parametrize("session,report", [(date(2026, 9, 29), "full-delivery"),
                                         (date(2026, 9, 30), "udiff")])
def test_reviewed_inputs_reject_other_sessions_and_reports(session: date, report: str) -> None:
    with pytest.raises(ValueError, match="only cover"):
        collector.reviewed_inputs(session, report)


def test_reviewed_inputs_reject_same_size_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content, identities = collector.reviewed_inputs(date(2026, 9, 30), "full-delivery")
    directory = tmp_path / "official_inputs" / "20260930"
    directory.mkdir(parents=True)
    (directory / "full-delivery.csv").write_bytes(b"X" + content[1:])
    (directory / "nifty50-identity.csv").write_bytes(identities)
    monkeypatch.setattr(collector, "__file__", str(tmp_path / "collector.py"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        collector.reviewed_inputs(date(2026, 9, 30), "full-delivery")


@pytest.mark.asyncio
async def test_archive_download_uses_installed_http_library() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"zip-bytes"))
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        assert await collector.read_official_archive(client, "https://nsearchives.nseindia.com/file") == b"zip-bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 403, 404])
async def test_archive_download_rejects_redirects_and_source_errors(status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(RuntimeError, match=f"http={status}"):
            await collector.read_official_archive(client, "https://nsearchives.nseindia.com/file")


@pytest.mark.asyncio
async def test_archive_download_checks_actual_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector, "MAX_FILE_BYTES", 4)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345"))
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(ValueError, match="size guard"):
            await collector.read_official_archive(client, "https://nsearchives.nseindia.com/file")
