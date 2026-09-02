import pytest

from app.connectors.nse_xbrl import sniff_xbrl_media_type, validate_xbrl_payload

SOURCE_URL = "https://nsearchives.nseindia.com/corporate/financial_result.xml"


def test_validate_xml_xbrl_with_declared_media_type() -> None:
    payload = b'<?xml version="1.0"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"></xbrli:xbrl>'

    media_type = validate_xbrl_payload(
        payload,
        content_type="application/xml; charset=utf-8",
        source_url=SOURCE_URL,
    )

    assert media_type == "application/xml"
    assert sniff_xbrl_media_type(payload) == "application/xml"


def test_validate_generic_octet_stream_by_content_sniffing() -> None:
    payload = b'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"></xbrli:xbrl>'

    media_type = validate_xbrl_payload(
        payload,
        content_type="application/octet-stream",
        source_url=SOURCE_URL,
    )

    assert media_type == "application/xml"


def test_inline_xbrl_with_xml_declaration_is_identified_as_xhtml() -> None:
    payload = (
        b'<?xml version="1.0"?><html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
        b'<body><ix:nonFraction name="Revenue">100</ix:nonFraction></body></html>'
    )

    media_type = validate_xbrl_payload(
        payload,
        content_type="application/xhtml+xml",
        source_url="https://www.nseindia.com/corporate/result.xhtml",
    )

    assert media_type == "application/xhtml+xml"


def test_validate_xbrl_rejects_empty_oversized_and_non_xbrl_payloads() -> None:
    with pytest.raises(ValueError, match="empty"):
        validate_xbrl_payload(b"", content_type="application/xml", source_url=SOURCE_URL)

    with pytest.raises(ValueError, match="safety limit"):
        validate_xbrl_payload(
            b"<xbrl></xbrl>",
            content_type="application/xml",
            source_url=SOURCE_URL,
            max_bytes=5,
        )

    with pytest.raises(ValueError, match="could not be identified"):
        validate_xbrl_payload(
            b"not an xbrl document",
            content_type="application/octet-stream",
            source_url=SOURCE_URL,
        )

    with pytest.raises(ValueError, match="Unsupported"):
        validate_xbrl_payload(
            b"%PDF-1.7 fake",
            content_type="application/pdf",
            source_url=SOURCE_URL,
        )


def test_validate_xbrl_reuses_official_nse_url_allowlist() -> None:
    with pytest.raises(ValueError, match="official NSE HTTPS host"):
        validate_xbrl_payload(
            b"<xbrl></xbrl>",
            content_type="application/xml",
            source_url="https://example.com/result.xml",
        )
