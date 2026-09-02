from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Literal

import fitz
import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings


class MultimodalAnalysisError(RuntimeError):
    """Raised when optional visual document analysis cannot produce a valid result."""


class VisualFinding(BaseModel):
    page_number: int = Field(ge=1)
    kind: Literal["chart", "table", "diagram", "scanned_text", "other"]
    summary: str = Field(min_length=8, max_length=1200)
    confidence: float = Field(ge=0.0, le=1.0)


class VisualFindingBundle(BaseModel):
    findings: list[VisualFinding] = Field(default_factory=list, max_length=12)


@dataclass(frozen=True)
class RenderedVisualPage:
    page_number: int
    png_bytes: bytes
    visual_score: float


@dataclass(frozen=True)
class MultimodalDocumentResult:
    provider: str
    model: str
    findings: list[VisualFinding]
    analyzed_pages: list[int]


class GeminiDocumentVisualAnalyzer:
    """Bounded, optional Gemini vision pass over visually rich PDF pages.

    Output is AI-assisted evidence only. It is never treated as primary filing evidence.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.enable_multimodal_document_analysis
            and self.settings.enable_external_llm_calls
            and self.settings.gemini_api_key
        )

    async def analyze_pdf(
        self,
        data: bytes,
        *,
        title: str,
        page_text: list[str],
    ) -> MultimodalDocumentResult | None:
        if not self.enabled:
            return None
        pages = render_visual_pages(
            data,
            page_text=page_text,
            max_pages=self.settings.multimodal_max_pages_per_document,
            max_total_bytes=self.settings.multimodal_max_inline_bytes,
        )
        if not pages:
            return None

        parts: list[dict[str, object]] = [
            {
                "text": (
                    "Analyze only the supplied rendered pages from an official Indian company filing. "
                    "Return JSON with a top-level 'findings' array. Each finding must contain exactly: "
                    "page_number, kind (chart|table|diagram|scanned_text|other), summary, confidence. "
                    "Describe visually meaningful information such as trends, structure, labels, or table "
                    "purpose. Do not infer causation. Do not invent numbers that are not clearly legible. "
                    "If a page has no useful visual information, omit it. The page labels below are the "
                    "authoritative page numbers. Document title: "
                    f"{title[:300]}"
                )
            }
        ]
        allowed_pages = {page.page_number for page in pages}
        for page in pages:
            parts.append({"text": f"OFFICIAL FILING PAGE {page.page_number}"})
            parts.append(
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(page.png_bytes).decode("ascii"),
                    }
                }
            )

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 1800,
                "responseMimeType": "application/json",
            },
        }
        model = self.settings.gemini_multimodal_model or self.settings.gemini_model
        endpoint = f"{self.settings.gemini_base_url.rstrip('/')}/models/{model}:generateContent"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(75.0, connect=10.0),
                transport=self.transport,
            ) as client:
                response = await client.post(
                    endpoint,
                    params={"key": self.settings.gemini_api_key},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MultimodalAnalysisError("Gemini visual document request failed") from exc

        try:
            body = response.json()
            text_parts = body["candidates"][0]["content"]["parts"]
            raw = "".join(str(part.get("text") or "") for part in text_parts)
            bundle = VisualFindingBundle.model_validate(json.loads(_strip_fences(raw)))
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise MultimodalAnalysisError("Gemini visual document response was invalid") from exc

        findings = [
            finding.model_copy(update={"confidence": min(finding.confidence, 0.80)})
            for finding in bundle.findings
            if finding.page_number in allowed_pages
        ]
        return MultimodalDocumentResult(
            provider="gemini",
            model=model,
            findings=findings,
            analyzed_pages=sorted(allowed_pages),
        )


def render_visual_pages(
    data: bytes,
    *,
    page_text: list[str],
    max_pages: int = 4,
    max_total_bytes: int = 12_000_000,
) -> list[RenderedVisualPage]:
    max_pages = max(1, min(max_pages, 8))
    max_total_bytes = max(1_000_000, min(max_total_bytes, 18_000_000))
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise MultimodalAnalysisError("Unable to open PDF for visual analysis") from exc

    try:
        scored: list[tuple[float, int]] = []
        for index, page in enumerate(document):
            text_length = len(page_text[index].strip()) if index < len(page_text) else 0
            try:
                image_count = len(page.get_images(full=True))
                drawing_count = len(page.get_drawings())
            except (RuntimeError, ValueError):
                image_count = 0
                drawing_count = 0

            score = min(image_count, 4) * 3.0
            if drawing_count >= 8:
                score += 2.0
            if drawing_count >= 30:
                score += 1.5
            if text_length < 500:
                score += 1.5
            if text_length < 120:
                score += 1.0
            if index < 2 and (image_count or drawing_count >= 8):
                score += 0.5
            if image_count or drawing_count >= 8 or text_length < 120:
                scored.append((score, index))

        selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:max_pages]
        output: list[RenderedVisualPage] = []
        total_bytes = 0
        for score, index in selected:
            page = document[index]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            png = pixmap.tobytes("png")
            if total_bytes + len(png) > max_total_bytes:
                continue
            total_bytes += len(png)
            output.append(
                RenderedVisualPage(
                    page_number=index + 1,
                    png_bytes=png,
                    visual_score=score,
                )
            )
        return output
    finally:
        document.close()


def _strip_fences(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
