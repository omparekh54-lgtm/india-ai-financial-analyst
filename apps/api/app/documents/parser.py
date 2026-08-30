from __future__ import annotations

import re

import fitz
from bs4 import BeautifulSoup

from app.documents.models import ParsedDocument, ParsedPage, TextChunk


class DocumentParseError(ValueError):
    pass


def parse_document(data: bytes, media_type: str, *, title: str | None = None) -> ParsedDocument:
    normalized = media_type.split(";", 1)[0].strip().lower()
    if normalized == "application/pdf":
        return parse_pdf(data, title=title)
    if normalized in {"text/html", "application/xhtml+xml"}:
        return parse_html(data, title=title)
    if normalized.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
        return ParsedDocument(
            media_type=normalized,
            title=title,
            pages=[ParsedPage(page_number=1, text=_clean_text(text))],
        )
    raise DocumentParseError(f"Unsupported media type: {normalized}")


def parse_pdf(data: bytes, *, title: str | None = None) -> ParsedDocument:
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise DocumentParseError("Unable to parse PDF") from exc

    try:
        pages = [
            ParsedPage(page_number=index + 1, text=_clean_text(page.get_text("text")))
            for index, page in enumerate(document)
        ]
        metadata = {key: value for key, value in (document.metadata or {}).items() if value}
        inferred_title = title or metadata.get("title")
        return ParsedDocument(
            media_type="application/pdf",
            title=inferred_title,
            pages=pages,
            metadata=metadata,
        )
    finally:
        document.close()


def parse_html(data: bytes, *, title: str | None = None) -> ParsedDocument:
    soup = BeautifulSoup(data, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    inferred_title = title
    if inferred_title is None and soup.title and soup.title.string:
        inferred_title = _clean_text(soup.title.string)

    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = _clean_text(main.get_text("\n", strip=True))
    return ParsedDocument(
        media_type="text/html",
        title=inferred_title,
        pages=[ParsedPage(page_number=1, text=text)],
    )


def chunk_document(
    document: ParsedDocument,
    *,
    max_chars: int = 4000,
    overlap_chars: int = 400,
) -> list[TextChunk]:
    if max_chars < 500:
        raise ValueError("max_chars must be at least 500")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be >= 0 and smaller than max_chars")

    chunks: list[TextChunk] = []
    index = 0
    for page in document.pages:
        text = page.text.strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + max_chars // 2, end)
                if boundary > start:
                    end = boundary
            content = text[start:end].strip()
            if content:
                chunks.append(
                    TextChunk(
                        chunk_index=index,
                        content=content,
                        page_number=page.page_number,
                    )
                )
                index += 1
            if end >= len(text):
                break
            start = max(0, end - overlap_chars)
    return chunks


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
