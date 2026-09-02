from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.securities.models import SecurityRecord


class SecurityMasterRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def list_all(self) -> list[SecurityRecord]:
        securities_sql = text(
            """
            select id, legal_name, nse_symbol, bse_code, isin, sector, industry, primary_exchange
            from securities
            order by legal_name
            """
        )
        aliases_sql = text(
            """
            select security_id, alias
            from security_aliases
            order by security_id, alias
            """
        )
        instruments_sql = text(
            """
            select security_id, provider, instrument_id
            from provider_instruments
            order by security_id, provider
            """
        )

        async with self.engine.connect() as connection:
            security_rows = (await connection.execute(securities_sql)).mappings().all()
            alias_rows = (await connection.execute(aliases_sql)).mappings().all()
            instrument_rows = (await connection.execute(instruments_sql)).mappings().all()

        aliases: dict[str, list[str]] = defaultdict(list)
        for row in alias_rows:
            aliases[str(row["security_id"])].append(str(row["alias"]))

        instruments: dict[str, dict[str, str]] = defaultdict(dict)
        for row in instrument_rows:
            instruments[str(row["security_id"])][str(row["provider"])] = str(row["instrument_id"])

        records: list[SecurityRecord] = []
        for row in security_rows:
            security_id = str(row["id"])
            records.append(
                SecurityRecord(
                    id=row["id"],
                    legal_name=row["legal_name"],
                    nse_symbol=row["nse_symbol"],
                    bse_code=row["bse_code"],
                    isin=row["isin"],
                    sector=row["sector"],
                    industry=row["industry"],
                    primary_exchange=row["primary_exchange"],
                    aliases=aliases[security_id],
                    provider_instruments=instruments[security_id],
                )
            )
        return records
