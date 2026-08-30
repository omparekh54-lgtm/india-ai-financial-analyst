from __future__ import annotations

import asyncio
import json
import signal

from app.core.config import get_settings
from app.db import create_database_engine
from app.workers.official_feeds import OfficialFeedWorker


async def _run() -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    engine = create_database_engine(settings.database_url)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop_event.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except NotImplementedError:
            pass

    worker = OfficialFeedWorker(
        engine,
        external_data_enabled=settings.enable_external_data_calls,
    )

    try:
        while not stop_event.is_set():
            result = await worker.run_once(limit=settings.official_feed_batch_size)
            print(json.dumps(result, default=str), flush=True)
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.official_feed_poll_seconds,
                )
            except TimeoutError:
                continue
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
