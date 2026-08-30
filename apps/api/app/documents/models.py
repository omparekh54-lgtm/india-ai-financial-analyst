from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedPage(BaseModel):
    page_number: int
    text: str


class ParsedDocument(BaseModel):
    media_type: str
    title: str | None = None
    pages: list[ParsedPage] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())


class TextChunk(BaseModel):
    chunk_index: int
    content: str
    page_number: int | None = None
