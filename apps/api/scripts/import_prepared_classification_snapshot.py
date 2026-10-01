"""Import a reviewed, complete set of original NSE responses for the prepared 50."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from app.connectors.nse_classification_snapshot import load_classification_snapshot
from app.core.config import get_settings
from app.db import create_database_engine
from scripts.backfill_nse_industry_classification import persist_classifications
from scripts.collect_official_peer_inputs import refresh_prepared_readiness


async def run(manifest: Path, approval_reference: str, apply: bool) -> int:
    settings = get_settings()
    if not settings.database_url:
        raise ValueError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            targets = (await connection.execute(text("""
                select s.id, s.nse_symbol, s.isin from securities s
                where s.id in (
                  select distinct security_id from security_agent_readiness_status
                ) and s.primary_exchange = 'NSE'
                and coalesce(s.metadata->>'nse_series', 'EQ') = 'EQ'
                order by s.nse_symbol limit 51
            """))).mappings().all()
            size = await connection.scalar(text("select pg_database_size(current_database())"))
        if len(targets) != 50 or len({r['nse_symbol'] for r in targets}) != 50:
            raise ValueError("Expected exactly 50 distinct prepared NSE EQ securities")
        if int(size or 0) >= 450_000_000:
            raise ValueError("Database storage guard reached")
        expected = {str(r['nse_symbol']): str(r['isin']) for r in targets}
        results, evidence = load_classification_snapshot(
            manifest, expected, approval_reference=approval_reference,
        )
        print(json.dumps({"status": "validated", "classifications": len(results),
                          "database_bytes": size, "apply": apply}), flush=True)
        if not apply:
            return 0
        by_id = {r['id']: results[r['nse_symbol']] for r in targets}
        source_evidence = {r['id']: evidence[r['nse_symbol']] for r in targets}
        updated = await persist_classifications(
            settings.database_url, by_id, export_evidence=source_evidence,
        )
        await refresh_prepared_readiness()
        async with engine.connect() as connection:
            ready = await connection.scalar(text("""
                select count(*) from security_agent_readiness_status
                where agent_name = 'orchestrator' and ready
                  and security_id = any(cast(:ids as uuid[]))
            """), {"ids": list(by_id)})
        print(json.dumps({"status": "verified" if ready == 50 else "blocked",
                          "updated": updated, "full_research_ready": ready,
                          "required": 50}), flush=True)
        return 0 if ready == 50 else 3
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval-reference", required=True,
                        help="Review attesting that the export contains original NSE responses")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.manifest, args.approval_reference, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
