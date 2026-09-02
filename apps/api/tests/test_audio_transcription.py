from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.agents.contracts import AgentInput, AgentName, Claim, EvidenceRef
from app.agents.validator_agent import EvidenceCrossValidationAgent
from app.core.config import Settings
from app.documents.audio import (
    AudioTranscriptionError,
    GeminiAudioTranscriber,
    TranscriptSegment,
    chunk_transcript,
)


@pytest.mark.asyncio
async def test_gemini_audio_transcription_is_mocked_structured_and_timestamped() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "language": "en-IN",
                                            "segments": [
                                                {
                                                    "speaker": "Management",
                                                    "start_seconds": 0.0,
                                                    "end_seconds": 8.5,
                                                    "text": "Revenue growth remained healthy during the quarter.",
                                                },
                                                {
                                                    "speaker": "Analyst",
                                                    "start_seconds": 8.5,
                                                    "end_seconds": 15.0,
                                                    "text": "Please discuss the margin outlook.",
                                                },
                                            ],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    settings = Settings(
        enable_external_llm_calls=True,
        enable_audio_transcription=True,
        gemini_api_key="test-key",
        gemini_audio_model="gemini-audio-test",
    )
    transcriber = GeminiAudioTranscriber(settings, transport=httpx.MockTransport(handler))
    result = await transcriber.transcribe(
        b"not-real-audio",
        media_type="audio/x-m4a",
        title="Q1 earnings call",
    )

    assert result is not None
    assert result.language == "en-IN"
    assert result.model == "gemini-audio-test"
    assert len(result.segments) == 2
    assert result.segments[0].start_seconds == 0.0
    payload = captured["payload"]
    assert isinstance(payload, dict)
    parts = payload["contents"][0]["parts"]  # type: ignore[index]
    assert parts[1]["inlineData"]["mimeType"] == "audio/m4a"  # type: ignore[index]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"  # type: ignore[index]


def test_chunk_transcript_preserves_timing_and_splits_bounded_text() -> None:
    segments = [
        TranscriptSegment(
            speaker="Management",
            start_seconds=0,
            end_seconds=30,
            text="A" * 620,
        ),
        TranscriptSegment(
            speaker="Management",
            start_seconds=30,
            end_seconds=60,
            text="B" * 620,
        ),
    ]
    chunks = chunk_transcript(segments, max_chars=800)

    assert len(chunks) == 2
    assert chunks[0].start_seconds == 0
    assert chunks[0].end_seconds == 30
    assert chunks[1].start_seconds == 30
    assert chunks[1].end_seconds == 60
    assert "[00:00 | Management]" in chunks[0].content


@pytest.mark.asyncio
async def test_audio_transcription_rejects_payload_over_inline_limit() -> None:
    settings = Settings(
        enable_external_llm_calls=True,
        enable_audio_transcription=True,
        gemini_api_key="test-key",
        audio_transcription_max_inline_bytes=500_000,
    )
    transcriber = GeminiAudioTranscriber(settings)

    with pytest.raises(AudioTranscriptionError, match="inline transcription limit"):
        await transcriber.transcribe(
            b"x" * 500_001,
            media_type="audio/mpeg",
            title="Oversized call",
        )


@pytest.mark.asyncio
async def test_audio_transcript_fact_cannot_be_verified_as_primary() -> None:
    evidence = EvidenceRef(
        source_type="audio_transcript",
        source_uri="https://www.nseindia.com/example-call.mp3",
        title="Official earnings call audio",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
        excerpt="[03:12 | Management] Demand remained healthy.",
        section="earnings_call",
        source_priority=2,
    )
    claim = Claim(
        agent=AgentName.EARNINGS,
        statement="Management said demand remained healthy.",
        claim_type="fact",
        confidence=0.9,
        evidence_ids=[evidence.evidence_id],
        status="pending",
    )
    output = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            context={"candidate_claims": [claim]},
            evidence=[evidence],
        )
    )

    assert output.claims[0].status == "supported"
    assert output.claims[0].status != "verified"
