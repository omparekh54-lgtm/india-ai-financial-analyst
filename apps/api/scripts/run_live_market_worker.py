from __future__ import annotations

import asyncio
import logging
import signal

from app.brokers.repository import BrokerRepository
from app.core.config import get_settings
from app.db import create_database_engine
from app.market.upstox_stream import UpstoxLiveMarketWorker

logger = logging.getLogger(__name__)


async def _run() -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    if not settings.enable_live_market:
        logger.warning("Live market worker is disabled; set ENABLE_LIVE_MARKET=true to run it")
        return 0
    if not settings.broker_token_encryption_key:
        raise RuntimeError("BROKER_TOKEN_ENCRYPTION_KEY is not configured")

    engine = create_database_engine(settings.database_url)
    worker = UpstoxLiveMarketWorker(BrokerRepository(engine), settings)
    task = asyncio.create_task(worker.run_forever(), name="upstox-live-market-worker")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop_event.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except NotImplementedError:
            pass

    try:
        stop_task = asyncio.create_task(stop_event.wait(), name="live-market-stop")
        done, _ = await asyncio.wait(
            {task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            await task
        return 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
