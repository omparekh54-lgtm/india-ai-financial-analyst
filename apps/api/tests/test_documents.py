import fitz

from app.documents.parser import chunk_document, parse_document


def test_pdf_parser_preserves_page_numbers() -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Revenue grew while operating cash flow improved.")
    pdf_bytes = document.tobytes()
    document.close()

    parsed = parse_document(pdf_bytes, "application/pdf", title="Test Filing")

    assert parsed.title == "Test Filing"
    assert parsed.pages[0].page_number == 1
    assert "Revenue grew" in parsed.pages[0].text


def test_html_parser_removes_scripts() -> None:
    parsed = parse_document(
        b"<html><head><title>Result</title><script>bad()</script></head>"
        b"<body><main><h1>Quarterly Result</h1><p>PAT increased.</p></main></body></html>",
        "text/html",
    )

    assert parsed.title == "Result"
    assert "Quarterly Result" in parsed.full_text
    assert "bad()" not in parsed.full_text


def test_chunker_tracks_source_page() -> None:
    document = fitz.open()
    page = document.new_page()
    lines = [
        f"Financial statement line {index:02d}: revenue cash flow margin debt and return metrics."
        for index in range(45)
    ]
    page.insert_text((72, 72), "\n".join(lines), fontsize=8)
    pdf_bytes = document.tobytes()
    document.close()

    parsed = parse_document(pdf_bytes, "application/pdf")
    chunks = chunk_document(parsed, max_chars=800, overlap_chars=100)

    assert len(parsed.pages[0].text) > 800
    assert len(chunks) > 1
    assert all(chunk.page_number == 1 for chunk in chunks)
