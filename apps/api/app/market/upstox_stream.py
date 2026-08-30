from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from app.brokers.repository import BrokerRepository
from app.brokers.upstox_oauth import UpstoxOAuthService
from app.core.config import Settings
from app.market.live_overlay import _fallback_instrument_key

logger = logging.getLogger(__name__)

_UPSTOX_FEED_AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
_PROVIDER = "upstox"


class UpstoxStreamError(RuntimeError):
    pass


class UpstoxLiveMarketWorker:
    """Runs one verified-TLS V3 market socket per leased, connected Upstox user."""

    def __init__(self, repository: BrokerRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings
        self.oauth = UpstoxOAuthService(repository, settings)
        self.worker_id = f"upstox-{uuid4()}"
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    async def run_forever(self) -> None:
        if not self.settings.enable_live_market:
            raise RuntimeError("ENABLE_LIVE_MARKET must be true to run the live market worker")
        try:
            while True:
                self._remove_finished_tasks()
                candidates = await self.repository.stream_candidates(
                    _PROVIDER,
                    limit=self.settings.live_market_max_user_streams,
                )
                for user_id in candidates:
                    if user_id not in self._tasks:
                        self._tasks[user_id] = asyncio.create_task(
                            self._run_user(user_id),
                            name=f"upstox-stream-{user_id}",
                        )
                await asyncio.sleep(self.settings.live_market_worker_poll_seconds)
        except asyncio.CancelledError:
            raise
        finally:
            tasks = list(self._tasks.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    def _remove_finished_tasks(self) -> None:
        finished = [user_id for user_id, task in self._tasks.items() if task.done()]
        for user_id in finished:
            task = self._tasks.pop(user_id)
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - isolate one user's stream from the coordinator
                logger.exception("Upstox user stream exited unexpectedly", extra={"user_id": str(user_id)})

    async def _run_user(self, user_id: UUID) -> None:
        acquired = await self.repository.acquire_stream_lease(
            user_id=user_id,
            provider=_PROVIDER,
            worker_id=self.worker_id,
            lease_seconds=self.settings.live_market_stream_lease_seconds,
        )
        if not acquired:
            return

        retry = 0
        try:
            while True:
                token = await self.oauth.access_token_for_user(user_id)
                instruments = await self._active_instruments(user_id)
                if not token or not instruments:
                    return
                try:
                    await self._stream_once(user_id, token, instruments)
                    retry = 0
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - reconnect boundary for external stream faults
                    retry += 1
                    logger.warning(
                        "Upstox stream interrupted; retrying",
                        extra={"user_id": str(user_id), "retry": retry},
                        exc_info=True,
                    )
                    alive = await self.repository.heartbeat_stream_lease(
                        user_id=user_id,
                        provider=_PROVIDER,
                        worker_id=self.worker_id,
                        lease_seconds=self.settings.live_market_stream_lease_seconds,
                    )
                    if not alive:
                        return
                    await asyncio.sleep(min(30.0, 2.0 ** min(retry, 4)))
        finally:
            await self.repository.release_stream_lease(
                user_id=user_id,
                provider=_PROVIDER,
                worker_id=self.worker_id,
            )

    async def _stream_once(
        self,
        user_id: UUID,
        access_token: str,
        instruments: dict[str, dict[str, object]],
    ) -> None:
        authorized_uri = await _authorize_stream_uri(access_token)
        pending: dict[str, dict[str, object]] = {}
        market_status: dict[str, str] = {}
        last_flush = time.monotonic()
        last_refresh = time.monotonic()
        last_heartbeat = time.monotonic()

        async with connect(
            authorized_uri,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=8 * 1024 * 1024,
        ) as websocket:
            await _send_subscription_set(websocket, instruments)

            while True:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                except TimeoutError:
                    message = None
                except ConnectionClosed as exc:
                    raise UpstoxStreamError("Upstox websocket closed") from exc

                if isinstance(message, bytes):
                    decoded = decode_upstox_v3_message(message)
                    _accumulate_quotes(decoded, instruments, market_status, pending)
                elif message is not None:
                    logger.debug("Ignoring non-binary Upstox V3 feed message")

                now_mono = time.monotonic()
                if pending and now_mono - last_flush >= self.settings.live_market_db_flush_seconds:
                    await self.repository.upsert_live_quotes(
                        user_id=user_id,
                        provider=_PROVIDER,
                        quotes=list(pending.values()),
                    )
                    pending.clear()
                    last_flush = now_mono

                if now_mono - last_refresh >= self.settings.live_market_subscription_refresh_seconds:
                    refreshed = await self._active_instruments(user_id)
                    if not refreshed:
                        if pending:
                            await self.repository.upsert_live_quotes(
                                user_id=user_id,
                                provider=_PROVIDER,
                                quotes=list(pending.values()),
                            )
                        return
                    await _reconcile_subscriptions(websocket, instruments, refreshed)
                    instruments = refreshed
                    last_refresh = now_mono

                if now_mono - last_heartbeat >= self.settings.live_market_stream_heartbeat_seconds:
                    alive = await self.repository.heartbeat_stream_lease(
                        user_id=user_id,
                        provider=_PROVIDER,
                        worker_id=self.worker_id,
                        lease_seconds=self.settings.live_market_stream_lease_seconds,
                    )
                    if not alive:
                        return
                    last_heartbeat = now_mono

    async def _active_instruments(self, user_id: UUID) -> dict[str, dict[str, object]]:
        rows = await self.repository.active_live_instruments(user_id, _PROVIDER)
        instruments: dict[str, dict[str, object]] = {}
        for row in rows:
            key = str(row.get("instrument_id") or "").strip()
            if not key:
                key = _fallback_instrument_key(row) or ""
            if not key:
                continue
            mode = str(row.get("mode") or "ltpc")
            if mode not in {"ltpc", "full"}:
                mode = "ltpc"
            instruments[key] = {
                "security_id": row["security_id"],
                "mode": mode,
            }
        return instruments


async def _authorize_stream_uri(access_token: str) -> str:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.get(_UPSTOX_FEED_AUTHORIZE_URL, headers=headers)
    except httpx.HTTPError as exc:
        raise UpstoxStreamError("Unable to authorize Upstox market stream") from exc
    if response.status_code in {401, 403}:
        raise UpstoxStreamError("Upstox stream access token is invalid or expired")
    if response.status_code == 429:
        raise UpstoxStreamError("Upstox stream authorization rate limit exceeded")
    if response.status_code >= 400:
        raise UpstoxStreamError(
            f"Upstox stream authorization returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstoxStreamError("Upstox stream authorization returned invalid JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    uri = data.get("authorized_redirect_uri") if isinstance(data, dict) else None
    if not isinstance(uri, str) or not uri.startswith("wss://"):
        raise UpstoxStreamError("Upstox stream authorization did not return a secure WSS URI")
    return uri


def decode_upstox_v3_message(message: bytes) -> dict[str, Any]:
    """Decode V3 protobuf using Upstox's maintained generated schema, loaded only at runtime."""
    try:
        from google.protobuf.json_format import MessageToDict
        from upstox_client.feeder.proto import MarketDataFeedV3_pb2
    except ImportError as exc:
        raise UpstoxStreamError(
            "Upstox V3 protobuf runtime is not installed; install the live_market extra"
        ) from exc
    try:
        decoded = MarketDataFeedV3_pb2.FeedResponse.FromString(message)
        result = MessageToDict(decoded)
    except Exception as exc:  # noqa: BLE001 - protobuf decoder raises several concrete types
        raise UpstoxStreamError("Unable to decode Upstox V3 protobuf message") from exc
    if not isinstance(result, dict):
        raise UpstoxStreamError("Decoded Upstox V3 feed was not an object")
    return result


async def _send_subscription_set(
    websocket: Any,
    instruments: Mapping[str, Mapping[str, object]],
) -> None:
    groups = _group_by_mode(instruments)
    for mode, keys in groups.items():
        if keys:
            await _send_stream_command(websocket, "sub", keys, mode=mode)


async def _reconcile_subscriptions(
    websocket: Any,
    current: Mapping[str, Mapping[str, object]],
    refreshed: Mapping[str, Mapping[str, object]],
) -> None:
    current_keys = set(current)
    refreshed_keys = set(refreshed)
    removed = sorted(current_keys - refreshed_keys)
    if removed:
        await _send_stream_command(websocket, "unsub", removed)

    added = {key: refreshed[key] for key in refreshed_keys - current_keys}
    await _send_subscription_set(websocket, added)

    changed_by_mode: dict[str, list[str]] = {"ltpc": [], "full": []}
    for key in current_keys & refreshed_keys:
        old_mode = str(current[key].get("mode") or "ltpc")
        new_mode = str(refreshed[key].get("mode") or "ltpc")
        if old_mode != new_mode:
            changed_by_mode.setdefault(new_mode, []).append(key)
    for mode, keys in changed_by_mode.items():
        if keys:
            await _send_stream_command(websocket, "change_mode", sorted(keys), mode=mode)


async def _send_stream_command(
    websocket: Any,
    method: str,
    instrument_keys: list[str],
    *,
    mode: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "guid": str(uuid4()),
        "method": method,
        "data": {"instrumentKeys": instrument_keys},
    }
    if mode is not None:
        data = payload["data"]
        if isinstance(data, dict):
            data["mode"] = mode
    await websocket.send(json.dumps(payload).encode())


def _group_by_mode(
    instruments: Mapping[str, Mapping[str, object]],
) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {"ltpc": [], "full": []}
    for key, item in instruments.items():
        mode = str(item.get("mode") or "ltpc")
        groups.setdefault(mode, []).append(key)
    for keys in groups.values():
        keys.sort()
    return groups


def _accumulate_quotes(
    payload: Mapping[str, Any],
    instruments: Mapping[str, Mapping[str, object]],
    market_status: dict[str, str],
    pending: dict[str, dict[str, object]],
) -> None:
    message_type = str(payload.get("type") or "")
    if message_type == "market_info":
        market_info = payload.get("marketInfo")
        segments = market_info.get("segmentStatus") if isinstance(market_info, dict) else None
        if isinstance(segments, dict):
            market_status.update({str(key): str(value) for key, value in segments.items()})
        return

    feeds = payload.get("feeds")
    if not isinstance(feeds, dict):
        return
    received_at = _timestamp_from_millis(payload.get("currentTs")) or datetime.now(UTC)
    for instrument_id, raw_feed in feeds.items():
        instrument = instruments.get(str(instrument_id))
        if instrument is None or not isinstance(raw_feed, dict):
            continue
        ltpc = _find_dict(raw_feed, "ltpc")
        if not ltpc:
            continue
        last_price = _number(ltpc.get("ltp"))
        if last_price is None:
            continue
        segment = str(instrument_id).split("|", 1)[0]
        pending[str(instrument_id)] = {
            "security_id": instrument["security_id"],
            "instrument_id": str(instrument_id),
            "last_price": last_price,
            "close_price": _number(ltpc.get("cp")),
            "last_trade_at": _timestamp_from_millis(ltpc.get("ltt")),
            "received_at": received_at,
            "bid": _best_price(raw_feed, "bidP"),
            "ask": _best_price(raw_feed, "askP"),
            "volume": _volume(raw_feed),
            "market_status": market_status.get(segment),
            "payload": {
                "ltq": ltpc.get("ltq"),
                "request_mode": raw_feed.get("requestMode"),
                "feed_timestamp": payload.get("currentTs"),
            },
        }


def _find_dict(value: object, key: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    direct = value.get(key)
    if isinstance(direct, dict):
        return direct
    for child in value.values():
        found = _find_dict(child, key)
        if found is not None:
            return found
    return None


def _best_price(value: object, field: str) -> float | None:
    found = _find_value(value, field)
    return _number(found)


def _volume(value: object) -> float | None:
    for key in ("vtt", "volume", "vol"):
        found = _find_value(value, key)
        result = _number(found)
        if result is not None:
            return result
    return None


def _find_value(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_value(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, key)
            if found is not None:
                return found
    return None


def _timestamp_from_millis(value: object) -> datetime | None:
    number = _number(value)
    if number is None:
        return None
    if number > 10_000_000_000:
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
