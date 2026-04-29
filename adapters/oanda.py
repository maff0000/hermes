"""
OANDA Streaming Adapter
EPIC-D002: Standalone Signal Service

Connects to OANDA v20 Streaming API for real-time prices.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional

import httpx

from .base import BaseAdapter, AdapterState
from models.tick import SignalTick, TickSource

logger = logging.getLogger("adapter.oanda")


class OANDAAdapter(BaseAdapter):
    """
    OANDA v20 Streaming API adapter.

    Streams pricing data for configured instruments.
    Auto-reconnects on disconnect.
    """

    def __init__(
        self,
        api_key: str,
        account_id: str,
        instruments: List[str],
        environment: str = "practice",
        use_mock: bool = False,
        mock_url: str = "http://localhost:8299"
    ):
        # Use MOCK source when testing - data flagged for easy cleanup
        tick_source = TickSource.MOCK if use_mock else TickSource.OANDA
        super().__init__(source=tick_source, instruments=instruments)

        self.api_key = api_key
        self.account_id = account_id
        self.environment = environment
        self.use_mock = use_mock
        self.mock_url = mock_url

        # URLs - use mock if enabled
        if use_mock:
            self.stream_url = mock_url
            self.api_url = mock_url
            logger.info(f"OANDA Adapter using MOCK: {mock_url}")
        elif environment == "live":
            self.stream_url = "https://stream-fxtrade.oanda.com"
            self.api_url = "https://api-fxtrade.oanda.com"
        else:
            self.stream_url = "https://stream-fxpractice.oanda.com"
            self.api_url = "https://api-fxpractice.oanda.com"

        # HTTP client
        self._client: Optional[httpx.AsyncClient] = None
        self._stream_response: Optional[httpx.Response] = None

    def _headers(self) -> dict:
        """Authorization headers"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def connect(self) -> bool:
        """Initialize HTTP client"""
        try:
            self._client = httpx.AsyncClient(
                headers=self._headers(),
                timeout=httpx.Timeout(30.0, read=None)  # No read timeout for streaming
            )

            # Validate credentials with account info
            url = f"{self.api_url}/v3/accounts/{self.account_id}"
            response = await self._client.get(url)

            if response.status_code == 200:
                data = response.json()
                balance = data.get("account", {}).get("balance", "N/A")
                logger.info(f"OANDA connected: Account {self.account_id}, Balance: {balance}")
                return True
            else:
                self._record_error(f"Auth failed: {response.status_code} {response.text}")
                return False

        except Exception as e:
            self._record_error(f"Connection error: {e}")
            return False

    async def disconnect(self):
        """Close connections"""
        if self._stream_response:
            await self._stream_response.aclose()
            self._stream_response = None
        if self._client:
            await self._client.aclose()
            self._client = None
        self._set_state(AdapterState.DISCONNECTED)

    async def stream(self) -> AsyncIterator[SignalTick]:
        """
        Stream pricing data from OANDA.

        Yields SignalTick for each price update.
        Handles heartbeats internally.
        """
        if not self._client:
            raise RuntimeError("Not connected - call connect() first")

        # Build streaming URL
        instruments_param = ",".join(self.instruments)
        url = f"{self.stream_url}/v3/accounts/{self.account_id}/pricing/stream"
        params = {"instruments": instruments_param}

        logger.info(f"Starting stream for: {instruments_param}")

        async with self._client.stream("GET", url, params=params) as response:
            self._stream_response = response

            if response.status_code != 200:
                error = await response.aread()
                self._record_error(f"Stream failed: {response.status_code} {error}")
                return

            async for line in response.aiter_lines():
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    msg_type = data.get("type")

                    if msg_type == "HEARTBEAT":
                        # Heartbeat - connection is alive
                        self._health.last_tick_at = datetime.now(timezone.utc)
                        continue

                    elif msg_type == "PRICE":
                        tick = self._parse_price(data)
                        if tick:
                            yield tick

                except json.JSONDecodeError as e:
                    logger.warning(f"JSON decode error: {e}")
                    continue
                except Exception as e:
                    self._record_error(f"Parse error: {e}")
                    continue

    def _parse_price(self, data: dict) -> Optional[SignalTick]:
        """
        Parse OANDA price message to SignalTick.

        OANDA format:
        {
            "type": "PRICE",
            "time": "2024-01-01T12:00:00.123456789Z",
            "bids": [{"price": "2650.123", "liquidity": 1000000}],
            "asks": [{"price": "2650.456", "liquidity": 1000000}],
            "closeoutBid": "2650.123",
            "closeoutAsk": "2650.456",
            "tradeable": true,
            "instrument": "XAU_USD"
        }
        """
        try:
            instrument = data.get("instrument")
            if not instrument:
                return None

            # Get best bid/ask
            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not bids or not asks:
                # Use closeout prices as fallback
                bid = float(data.get("closeoutBid", 0))
                ask = float(data.get("closeoutAsk", 0))
            else:
                bid = float(bids[0]["price"])
                ask = float(asks[0]["price"])

            if bid == 0 or ask == 0:
                return None

            # Parse timestamp
            time_str = data.get("time", "")
            try:
                # OANDA uses nanosecond precision
                if "." in time_str:
                    # Truncate to microseconds for Python
                    parts = time_str.split(".")
                    frac = parts[1].rstrip("Z")[:6]  # Max 6 digits
                    time_str = f"{parts[0]}.{frac}Z"
                source_timestamp = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except Exception:
                source_timestamp = datetime.now(timezone.utc)

            return SignalTick(
                instrument=instrument,
                bid=bid,
                ask=ask,
                timestamp=datetime.now(timezone.utc),
                source=self.source,  # MOCK or OANDA based on use_mock
                source_timestamp=source_timestamp
            )

        except Exception as e:
            logger.warning(f"Failed to parse price: {e}")
            return None

    async def get_current_price(self, instrument: str) -> Optional[SignalTick]:
        """
        Get current price (one-shot, not streaming).
        Useful for initialization or fallback.
        """
        if not self._client:
            return None

        try:
            url = f"{self.api_url}/v3/accounts/{self.account_id}/pricing"
            params = {"instruments": instrument}

            response = await self._client.get(url, params=params)
            if response.status_code != 200:
                return None

            data = response.json()
            prices = data.get("prices", [])
            if not prices:
                return None

            return self._parse_price(prices[0])

        except Exception as e:
            logger.warning(f"Failed to get price for {instrument}: {e}")
            return None


async def test_oanda_stream():
    """Test OANDA streaming (standalone)"""
    import sys
    from pathlib import Path

    # Setup - use relative path from script location
    ADAPTER_DIR = Path(__file__).parent.absolute()
    BASE_DIR = ADAPTER_DIR.parent
    sys.path.insert(0, str(BASE_DIR))

    from env_config import get_env, get_env_bool, get_env_list

    use_mock = get_env_bool("USE_MOCK", False)
    mock_url = get_env("MOCK_OANDA_URL", "http://localhost:8299")
    instruments = get_env_list("INSTRUMENTS", ["XAU_USD", "XAG_USD", "XPT_USD", "XCU_USD"])

    print(f"USE_MOCK: {use_mock}")
    print(f"Instruments: {instruments}")

    adapter = OANDAAdapter(
        api_key=get_env("OANDA_API_KEY", ""),
        account_id=get_env("OANDA_ACCOUNT_ID", ""),
        instruments=instruments,
        environment=get_env("OANDA_ENVIRONMENT", "practice"),
        use_mock=use_mock,
        mock_url=mock_url
    )

    def on_tick(tick: SignalTick):
        latency = tick.latency_ms if tick.latency_ms else 0
        print(f"{tick.instrument}: {tick.bid:.2f}/{tick.ask:.2f} spread={tick.spread:.3f} latency={latency:.1f}ms")

    adapter.set_tick_callback(on_tick)

    print("Connecting...")
    if await adapter.connect():
        print("Connected! Streaming prices (Ctrl+C to stop)...")
        try:
            async for tick in adapter.stream():
                on_tick(tick)
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            await adapter.disconnect()
    else:
        print("Failed to connect")


if __name__ == "__main__":
    asyncio.run(test_oanda_stream())
