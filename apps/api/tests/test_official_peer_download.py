import httpx
import pytest

from scripts import collect_official_peer_inputs as collector


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
