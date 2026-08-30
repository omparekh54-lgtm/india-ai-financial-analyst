from __future__ import annotations

import base64
import json
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import Settings

SUPPORTED_AUDIO_TYPES = {
    "audio/aac",
    "audio/aiff",
    "audio/flac",
    "audio/l16",
    "audio/m4a",
    "audio/mp3",
    "audio/mpeg",
    "audio/mulaw",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
}


class AudioTranscriptionError(RuntimeError):
    """Raised when optional earnings-call transcription cannot produce safe structured output."""


class TranscriptSegment(BaseModel):
    speaker: str | None = Field(default=None, max_length=80)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    text: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="after")
    def validate_timing(self) -> TranscriptSegment:
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("Transcript segment end must not precede start")
        return self


class TranscriptBundle(BaseModel):
    language: str | None = Field(default=None, max_length=40)
    segments: list[TranscriptSegment] = Field(min_length=1, max_length=600)


@dataclass(frozen=True)
class AudioTranscriptionResult:
    provider: str
    model: str
    language: str | None
    segments: list[TranscriptSegment]


@dataclass(frozen=True)
class TranscriptChunk:
    chunk_index: int
    content: str
    start_seconds: float | None
    end_seconds: float | None


class GeminiAudioTranscriber:
    """Optional bounded Gemini audio transcription with structured, timestamp-aware output."""

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
            self.settings.enable_audio_transcription
            and self.settings.enable_external_llm_calls
            and self.settings.gemini_api_key
        )

    async def transcribe(
        self,
        data: bytes,
        *,
        media_type: str,
        title: str,
        vocabulary: list[str] | None = None,
    ) -> AudioTranscriptionResult | None:
        if not self.enabled:
            return None
        normalized = normalize_audio_media_type(media_type)
        if normalized not in SUPPORTED_AUDIO_TYPES:
            raise AudioTranscriptionError(f"Unsupported audio media type: {normalized}")
        if not data:
            raise AudioTranscriptionError("Audio payload is empty")
        if len(data) > self.settings.audio_transcription_max_inline_bytes:
            raise AudioTranscriptionError("Audio exceeds configured inline transcription limit")

        vocabulary_text = ", ".join(
            term.strip() for term in (vocabulary or []) if term.strip()
        )[:1500]
        prompt = (
            "Transcribe this company earnings/investor audio faithfully. Return JSON only with exactly "
            "this shape: {\"language\":\"...\",\"segments\":[{\"speaker\":\"...\","
            "\"start_seconds\":0.0,\"end_seconds\":12.3,\"text\":\"...\"}]}. "
            "Use null for speaker or timestamps when they cannot be determined reliably. Preserve "
            "financial terminology, company names and numbers exactly as heard. Do not summarize, "
            "correct, infer or add facts. Split into chronological segments suitable for citation. "
            f"Audio title: {title[:300]}."
        )
        if vocabulary_text:
            prompt += f" Vocabulary hints: {vocabulary_text}."

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": normalized,
                                "data": base64.b64encode(data).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": self.settings.audio_transcription_max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        model = self.settings.gemini_audio_model
        endpoint = f"{self.settings.gemini_base_url.rstrip('/')}/models/{model}:generateContent"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                transport=self.transport,
            ) as client:
                response = await client.post(
                    endpoint,
                    params={"key": self.settings.gemini_api_key},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AudioTranscriptionError("Gemini audio transcription request failed") from exc

        try:
            body = response.json()
            parts = body["candidates"][0]["content"]["parts"]
            raw = "".join(str(part.get("text") or "") for part in parts)
            bundle = TranscriptBundle.model_validate(json.loads(_strip_fences(raw)))
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise AudioTranscriptionError("Gemini audio transcription response was invalid") from exc

        return AudioTranscriptionResult(
            provider="gemini",
            model=model,
            language=bundle.language,
            segments=bundle.segments,
        )


def chunk_transcript(
    segments: list[TranscriptSegment],
    *,
    max_chars: int = 3200,
) -> list[TranscriptChunk]:
    max_chars = max(800, min(max_chars, 6000))
    chunks: list[TranscriptChunk] = []
    lines: list[str] = []
    start: float | None = None
    end: float | None = None

    def flush() -> None:
        nonlocal lines, start, end
        if not lines:
            return
        chunks.append(
            TranscriptChunk(
                chunk_index=len(chunks),
                content="\n".join(lines).strip(),
                start_seconds=start,
                end_seconds=end,
            )
        )
        lines = []
        start = None
        end = None

    for segment in segments:
        prefix_parts: list[str] = []
        if segment.start_seconds is not None:
            prefix_parts.append(_format_timestamp(segment.start_seconds))
        if segment.speaker:
            prefix_parts.append(segment.speaker.strip())
        prefix = " | ".join(prefix_parts)
        line = f"[{prefix}] {segment.text.strip()}" if prefix else segment.text.strip()
        if lines and len("\n".join([*lines, line])) > max_chars:
            flush()
        if not lines:
            start = segment.start_seconds
        lines.append(line)
        if segment.end_seconds is not None:
            end = segment.end_seconds
    flush()
    return chunks


def normalize_audio_media_type(media_type: str) -> str:
    normalized = media_type.split(";", 1)[0].strip().lower()
    return {
        "audio/x-m4a": "audio/m4a",
        "audio/x-wav": "audio/wav",
    }.get(normalized, normalized)


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _strip_fences(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
