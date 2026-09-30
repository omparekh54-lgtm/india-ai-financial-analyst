import asyncio
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.ingestion.derived_metric_ingestion import DerivedSecurityMetricIngestor
from app.ingestion.derived_metrics import DerivedMetricBundle
from app.ingestion.metrics import SecurityMetricInput


@pytest.mark.parametrize("approved_count,expected", [(0, False), (1, False), (2, True)])
def test_metric_source_requires_every_upstream_approval(approved_count, expected) -> None:
    upstream = (uuid4(), uuid4())
    connection = AsyncMock()
    connection.scalar.side_effect = [approved_count, uuid4(), uuid4()]
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    bundle = DerivedMetricBundle(
        metrics=(SecurityMetricInput(
            metric_name="pe", as_of_date=date(2026, 9, 30), value=10, unit="multiple",
            metadata={"formula": "test", "upstream_source_ids": [str(x) for x in upstream]},
        ),),
        upstream_source_ids=upstream, checksum="a" * 64,
    )
    asyncio.run(DerivedSecurityMetricIngestor(engine).ingest(
        security_id=uuid4(), symbol="TEST", bundle=bundle,
    ))
    source_update = connection.execute.call_args_list[0].args[1]
    assert json.loads(source_update["metadata"])["production_approved"] is expected
