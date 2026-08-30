from __future__ import annotations

import argparse
import asyncio
import json
from urllib.parse import urlparse

from sqlalchemy import text

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.official_pipeline import OFFICIAL_INDIA_DOMAINS

PROVIDERS = {"NSE", "BSE", "RBI", "NSDL"}
FEED_TYPES = {"exchange_disclosures", "financial_xbrl", "rbi_macro", "nsdl_flows"}


def _allowed_official_url(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().strip(".")
    return parsed.scheme == "https" and any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in OFFICIAL_INDIA_DOMAINS
    )


async def _register(args: argparse.Namespace) -> str:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    provider = args.provider.upper()
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of: {', '.join(sorted(PROVIDERS))}")
    if args.feed_type not in FEED_TYPES:
        raise ValueError(f"feed_type must be one of: {', '.join(sorted(FEED_TYPES))}")
    if not _allowed_official_url(args.source_url):
        raise ValueError("source_url must be HTTPS and on the approved official India allowlist")

    exchange = args.exchange.upper() if args.exchange else None
    if exchange not in {None, "NSE", "BSE"}:
        raise ValueError("exchange must be NSE or BSE")
    if args.feed_type == "exchange_disclosures" and exchange is None:
        raise ValueError("exchange_disclosures requires --exchange")
    if args.feed_type == "financial_xbrl" and (exchange is None or not args.identifier):
        raise ValueError("financial_xbrl requires --exchange and --identifier")

    parser_config = json.loads(args.parser_config)
    if not isinstance(parser_config, dict):
        raise TypeError("--parser-config must be a JSON object")

    parameters = {
        "name": args.name,
        "provider": provider,
        "feed_type": args.feed_type,
        "source_url": args.source_url,
        "exchange": exchange,
        "identifier": args.identifier,
        "title": args.title,
        "parser_config": json.dumps(parser_config),
        "poll_interval_seconds": max(300, min(args.poll_interval_seconds, 86400)),
        "enabled": not args.disabled,
    }
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    insert into official_data_feeds (
                        name, provider, feed_type, source_url, exchange, identifier,
                        title, parser_config, poll_interval_seconds, enabled, next_run_at
                    ) values (
                        :name, :provider, :feed_type, :source_url, :exchange, :identifier,
                        :title, cast(:parser_config as jsonb), :poll_interval_seconds,
                        :enabled, now()
                    )
                    on conflict do nothing
                    returning id
                    """
                ),
                parameters,
            )
            feed_id = result.scalar_one_or_none()
            if feed_id is None:
                feed_id = await connection.scalar(
                    text(
                        """
                        select id
                        from official_data_feeds
                        where provider = :provider
                          and feed_type = :feed_type
                          and source_url = :source_url
                          and coalesce(exchange, '') = coalesce(:exchange, '')
                          and coalesce(identifier, '') = coalesce(:identifier, '')
                        limit 1
                        """
                    ),
                    parameters,
                )
                if feed_id is None:
                    raise RuntimeError("Unable to resolve existing official feed")
                await connection.execute(
                    text(
                        """
                        update official_data_feeds
                        set name = :name,
                            title = :title,
                            parser_config = cast(:parser_config as jsonb),
                            poll_interval_seconds = :poll_interval_seconds,
                            enabled = :enabled,
                            next_run_at = case
                              when :enabled then least(next_run_at, now())
                              else next_run_at
                            end,
                            lease_until = case when :enabled then lease_until else null end,
                            updated_at = now()
                        where id = :feed_id
                        """
                    ),
                    {**parameters, "feed_id": feed_id},
                )
            return str(feed_id)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Register or update an approved official data feed.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--feed-type", required=True, dest="feed_type")
    parser.add_argument("--source-url", required=True, dest="source_url")
    parser.add_argument("--exchange")
    parser.add_argument("--identifier")
    parser.add_argument("--title")
    parser.add_argument("--poll-interval-seconds", type=int, default=900)
    parser.add_argument("--parser-config", default="{}")
    parser.add_argument(
        "--disabled",
        action="store_true",
        help="Register/update the feed but keep it disabled until explicitly activated.",
    )
    args = parser.parse_args()
    feed_id = asyncio.run(_register(args))
    print(feed_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
