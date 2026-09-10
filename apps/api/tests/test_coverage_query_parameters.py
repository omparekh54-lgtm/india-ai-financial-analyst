from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.core.financial_history_coverage import load_financial_history_coverage
from app.core.market_history_coverage import load_market_history_coverage
from app.core.peer_metric_coverage import load_peer_metric_coverage


class _EmptyResult:
    def mappings(self) -> _EmptyResult:
        return self

    def all(self) -> list[Any]:
        return []


class _Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any, _parameters: dict[str, Any]) -> _EmptyResult:
        self.statements.append(str(statement))
        return _EmptyResult()


class _ConnectionContext:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(self.connection)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "loader",
    [
        load_financial_history_coverage,
        load_market_history_coverage,
        load_peer_metric_coverage,
    ],
)
async def test_optional_security_filter_casts_null_parameter_to_uuid(loader: Any) -> None:
    engine = _Engine()
    kwargs = {"as_of": date(2026, 9, 10)} if loader is not load_peer_metric_coverage else {}

    await loader(engine, security_id=None, **kwargs)

    assert len(engine.connection.statements) == 1
    assert "cast(:security_id as uuid) is null" in engine.connection.statements[0]
    assert "= cast(:security_id as uuid)" in engine.connection.statements[0]
