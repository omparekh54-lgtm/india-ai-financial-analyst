from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.market.upstox_stream import (
    _accumulate_quotes,
    _reconcile_subscriptions,
    _send_subscription_set,
    _timestamp_from_millis,
    decode_upstox_v3_message,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, value: bytes) -> None:
        self.messages.append(json.loads(value.decode()))


def _methods(websocket: FakeWebSocket) -> list[str]:
    return [str(message["method"]) for message in websocket.messages]


def test_accumulate_quotes_extracts_ltpc_depth_volume_and_status() -> None:
    security_id = uuid4()
    now = datetime.now(UTC)
    millis = int(now.timestamp() * 1000)
    pending: dict[str, dict[str, object]] = {}
    status = {"NSE_EQ": "NORMAL_OPEN"}
    instruments = {
        "NSE_EQ|INE002A01018": {
            "security_id": security_id,
            "mode": "full",
        }
    }
    payload = {
        "type": "live_feed",
        "currentTs": str(millis),
        "feeds": {
            "NSE_EQ|INE002A01018": {
                "fullFeed": {
                    "marketFF": {
                        "ltpc": {
                            "ltp": 3012.25,
                            "ltt": str(millis),
                            "ltq": "12",
                            "cp": 2999.10,
                        },
                        "marketLevel": {
                            "bidAskQuote": [
                                {"bidP": 3012.20, "askP": 3012.30, "bidQ": "30", "askQ": "20"}
                            ]
                        },
                        "vtt": "1234567",
                    }
                },
                "requestMode": "full",
            }
        },
    }

    _accumulate_quotes(payload, instruments, status, pending)

    quote = pending["NSE_EQ|INE002A01018"]
    assert quote["security_id"] == security_id
    assert quote["last_price"] == 3012.25
    assert quote["close_price"] == 2999.10
    assert quote["bid"] == 3012.20
    assert quote["ask"] == 3012.30
    assert quote["volume"] == 1234567.0
    assert quote["market_status"] == "NORMAL_OPEN"
    assert isinstance(quote["received_at"], datetime)
    assert isinstance(quote["last_trade_at"], datetime)


def test_market_info_updates_segment_status_without_quote() -> None:
    pending: dict[str, dict[str, object]] = {}
    status: dict[str, str] = {}
    _accumulate_quotes(
        {
            "type": "market_info",
            "marketInfo": {
                "segmentStatus": {
                    "NSE_EQ": "NORMAL_OPEN",
                    "BSE_EQ": "NORMAL_CLOSE",
                }
            },
        },
        {},
        status,
        pending,
    )
    assert status == {"NSE_EQ": "NORMAL_OPEN", "BSE_EQ": "NORMAL_CLOSE"}
    assert pending == {}


@pytest.mark.asyncio
async def test_initial_subscriptions_are_grouped_by_mode() -> None:
    websocket = FakeWebSocket()
    await _send_subscription_set(
        websocket,
        {
            "NSE_EQ|ONE": {"mode": "ltpc"},
            "NSE_EQ|TWO": {"mode": "full"},
            "NSE_EQ|THREE": {"mode": "ltpc"},
        },
    )

    assert _methods(websocket) == ["sub", "sub"]
    first = websocket.messages[0]["data"]
    second = websocket.messages[1]["data"]
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first["mode"] == "ltpc"
    assert first["instrumentKeys"] == ["NSE_EQ|ONE", "NSE_EQ|THREE"]
    assert second["mode"] == "full"
    assert second["instrumentKeys"] == ["NSE_EQ|TWO"]


@pytest.mark.asyncio
async def test_reconcile_unsubscribes_adds_and_changes_mode() -> None:
    websocket = FakeWebSocket()
    await _reconcile_subscriptions(
        websocket,
        {
            "NSE_EQ|REMOVE": {"mode": "ltpc"},
            "NSE_EQ|CHANGE": {"mode": "ltpc"},
            "NSE_EQ|KEEP": {"mode": "full"},
        },
        {
            "NSE_EQ|ADD": {"mode": "ltpc"},
            "NSE_EQ|CHANGE": {"mode": "full"},
            "NSE_EQ|KEEP": {"mode": "full"},
        },
    )

    assert _methods(websocket) == ["unsub", "sub", "change_mode"]
    assert websocket.messages[0]["data"]["instrumentKeys"] == ["NSE_EQ|REMOVE"]  # type: ignore[index]
    assert websocket.messages[1]["data"]["instrumentKeys"] == ["NSE_EQ|ADD"]  # type: ignore[index]
    assert websocket.messages[2]["data"]["instrumentKeys"] == ["NSE_EQ|CHANGE"]  # type: ignore[index]
    assert websocket.messages[2]["data"]["mode"] == "full"  # type: ignore[index]


def test_timestamp_from_millis_accepts_upstox_string_timestamp() -> None:
    parsed = _timestamp_from_millis("1788091200000")
    assert parsed is not None
    assert parsed.tzinfo == UTC


def test_protobuf_runtime_decodes_empty_feed_response() -> None:
    from upstox_client.feeder.proto import MarketDataFeedV3_pb2

    payload = MarketDataFeedV3_pb2.FeedResponse().SerializeToString()
    assert decode_upstox_v3_message(payload) == {}
