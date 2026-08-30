from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class ExchangeDisclosure:
    security_id: UUID
    exchange: str
    source_uri: str
    headline: str
    published_at: datetime | None = None
    title: str | None = None
    excerpt: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class IngestedDisclosure:
    source_id: UUID
    event_id: UUID
    event_type: str
    fingerprint: str


_EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("auditor_resignation", re.compile(r"auditor.{0,50}resign", re.I), 0.95),
    ("promoter_pledge", re.compile(r"promoter.{0,80}(pledge|encumbrance)", re.I), 0.90),
    ("credit_rating", re.compile(r"credit rating|rating (upgrade|downgrade|reaffirm)", re.I), 0.85),
    ("financial_results", re.compile(r"financial results|quarterly results|regulation 33", re.I), 0.95),
    ("earnings_call", re.compile(r"earnings call|conference call|concall", re.I), 0.80),
    ("investor_presentation", re.compile(r"investor presentation", re.I), 0.75),
    ("buyback", re.compile(r"buy[- ]?back", re.I), 0.90),
    ("bonus", re.compile(r"bonus (issue|share)", re.I), 0.85),
    ("split", re.compile(r"stock split|sub[- ]?division of.*share", re.I), 0.85),
    ("qip", re.compile(r"qualified institutional placement|\bqip\b", re.I), 0.85),
    ("preferential_issue", re.compile(r"preferential (issue|allotment)", re.I), 0.85),
    ("related_party", re.compile(r"related party transaction", re.I), 0.80),
    ("merger_demerger", re.compile(r"merger|demerger|scheme of arrangement", re.I), 0.90),
    ("order_win", re.compile(r"award of order|receipt of order|order win", re.I), 0.75),
    ("dividend", re.compile(r"dividend|record date", re.I), 0.75),
)


class ExchangeDisclosureIngestor:
    """Persists official exchange disclosures with source provenance and idempotency."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def ingest(self, disclosure: ExchangeDisclosure) -> IngestedDisclosure:
        exchange = disclosure.exchange.strip().upper()
        if exchange not in {"NSE", "BSE"}:
            raise ValueError("exchange must be NSE or BSE")
        if not disclosure.source_uri.startswith("https://"):
            raise ValueError("source_uri must be HTTPS")

        event_type, materiality = classify_exchange_event(
            disclosure.headline,
            disclosure.excerpt,
        )
        published_at = disclosure.published_at
        fingerprint = disclosure_fingerprint(
            disclosure.security_id,
            exchange,
            disclosure.source_uri,
            disclosure.headline,
            published_at,
        )
        retrieved_at = datetime.now(UTC)
        metadata = {"exchange": exchange, **(disclosure.metadata or {})}

        async with self.engine.begin() as connection:
            source_result = await connection.execute(
                text(
                    """
                    insert into sources (
                        security_id, source_type, source_uri, title, published_at,
                        retrieved_at, freshness, metadata
                    ) values (
                        :security_id, 'exchange_filing', :source_uri, :title, :published_at,
                        :retrieved_at, 'near_live', cast(:metadata as jsonb)
                    )
                    on conflict do nothing
                    returning id
                    """
                ),
                {
                    "security_id": disclosure.security_id,
                    "source_uri": disclosure.source_uri,
                    "title": disclosure.title or disclosure.headline,
                    "published_at": published_at,
                    "retrieved_at": retrieved_at,
                    "metadata": json.dumps(metadata),
                },
            )
            source_id = source_result.scalar_one_or_none()
            if source_id is None:
                source_id = await connection.scalar(
                    text(
                        """
                        select id from sources
                        where security_id = :security_id
                          and source_uri = :source_uri
                          and coalesce(published_at, '1970-01-01'::timestamptz)
                              = coalesce(:published_at, '1970-01-01'::timestamptz)
                        order by retrieved_at desc
                        limit 1
                        """
                    ),
                    {
                        "security_id": disclosure.security_id,
                        "source_uri": disclosure.source_uri,
                        "published_at": published_at,
                    },
                )
            if source_id is None:
                raise RuntimeError("Unable to resolve persisted disclosure source")

            event_result = await connection.execute(
                text(
                    """
                    insert into corporate_events (
                        security_id, event_type, headline, event_at, source_id,
                        materiality, data, fingerprint
                    ) values (
                        :security_id, :event_type, :headline, :event_at, :source_id,
                        :materiality, cast(:data as jsonb), :fingerprint
                    )
                    on conflict (security_id, fingerprint) where fingerprint is not null
                    do update set
                        event_type = excluded.event_type,
                        headline = excluded.headline,
                        event_at = excluded.event_at,
                        source_id = excluded.source_id,
                        materiality = excluded.materiality,
                        data = excluded.data
                    returning id
                    """
                ),
                {
                    "security_id": disclosure.security_id,
                    "event_type": event_type,
                    "headline": disclosure.headline,
                    "event_at": published_at,
                    "source_id": source_id,
                    "materiality": materiality,
                    "data": json.dumps(
                        {
                            "exchange": exchange,
                            "excerpt": (disclosure.excerpt or "")[:5000],
                            **(disclosure.metadata or {}),
                        }
                    ),
                    "fingerprint": fingerprint,
                },
            )
            event_id = event_result.scalar_one()

        return IngestedDisclosure(
            source_id=source_id,
            event_id=event_id,
            event_type=event_type,
            fingerprint=fingerprint,
        )


def classify_exchange_event(headline: str, excerpt: str | None = None) -> tuple[str, float]:
    text_value = f"{headline}\n{excerpt or ''}"
    for event_type, pattern, materiality in _EVENT_PATTERNS:
        if pattern.search(text_value):
            return event_type, materiality
    return "exchange_announcement", 0.50


def disclosure_fingerprint(
    security_id: UUID,
    exchange: str,
    source_uri: str,
    headline: str,
    published_at: datetime | None,
) -> str:
    normalized_headline = re.sub(r"\s+", " ", headline.strip().lower())
    timestamp = published_at.astimezone(UTC).isoformat() if published_at else "unknown"
    payload = "|".join(
        (str(security_id), exchange.upper(), source_uri.strip(), normalized_headline, timestamp)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
