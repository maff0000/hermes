#!/usr/bin/env python3
"""
Mock OANDA Trading API with Order Simulation
EPIC-D003: DEV Environment Complete

Simulates full OANDA trading functionality:
- Price streaming
- Order placement
- Position tracking
- Trade management
- P&L calculation

Uses tradingPaper database for persistence.

Run: python3 mock/oanda_mock.py
Endpoint: http://localhost:8299
"""
import asyncio
import json
import random
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn
import pymysql

app = FastAPI(title="Mock OANDA Trading API")

# =============================================================================
# Database Configuration
# =============================================================================
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "PDAPass@R00t123"),
    "database": os.getenv("DB_NAME", "tradingPaper"),
    "cursorclass": pymysql.cursors.DictCursor,
}

def get_db():
    """Get database connection"""
    return pymysql.connect(**DB_CONFIG)

# =============================================================================
# Price Simulation
# =============================================================================
# Current OANDA prices (2026-01-01)
BASE_PRICES = {
    "XAU_USD": 4322.61,   # Gold
    "XAG_USD": 71.64,     # Silver
    "XPT_USD": 2026.52,   # Platinum
    "XCU_USD": 5.6146,    # Copper
    # FX pairs for currency conversion
    "GBP_USD": 1.2550,    # British Pound
    "EUR_USD": 1.0850,    # Euro
    "USD_JPY": 148.50,    # Japanese Yen
}

# Real OANDA spreads
SPREADS = {
    "XAU_USD": 5.00,      # Gold spread ~$5
    "XAG_USD": 0.145,     # Silver spread
    "XPT_USD": 2.48,      # Platinum spread
    "XCU_USD": 0.002,     # Copper spread
    # FX spreads (in pips)
    "GBP_USD": 0.00015,   # ~1.5 pips
    "EUR_USD": 0.00012,   # ~1.2 pips
    "USD_JPY": 0.015,     # ~1.5 pips
}

# Volatility settings (points per tick)
VOLATILITY = {
    "XAU_USD": 0.50,      # Gold moves ~$0.50 per tick
    "XAG_USD": 0.02,      # Silver
    "XPT_USD": 0.50,      # Platinum
    "XCU_USD": 0.001,     # Copper
    "GBP_USD": 0.0002,    # FX pairs
    "EUR_USD": 0.0002,
    "USD_JPY": 0.02,
}

current_prices = BASE_PRICES.copy()
price_trend = {}  # Track trend direction per instrument


def get_signal_bias(instrument: str) -> float:
    """
    Get price bias from open trades (simulates market moving with/against position).
    Returns: positive = trending up, negative = trending down, 0 = neutral
    """
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT side, SUM(units) as total_units
                FROM paper_trades
                WHERE instrument = %s AND status = 'OPEN'
                GROUP BY side
            """, (instrument,))
            positions = cursor.fetchall()
        conn.close()

        # If we have open trades, simulate market movement
        # 60% chance to move toward TP (profitable), 40% toward SL
        for pos in positions:
            if pos["total_units"] and float(pos["total_units"]) > 0:
                win_bias = 0.6 if random.random() < 0.7 else -0.4  # Slight winning bias
                if pos["side"] == "BUY":
                    return win_bias  # Positive = price up = good for longs
                else:
                    return -win_bias  # Negative = price down = good for shorts
        return 0
    except Exception:
        return 0


def get_current_price(instrument: str) -> tuple:
    """Get current bid/ask for instrument with realistic movement"""
    global current_prices, price_trend

    base = current_prices.get(instrument, 100.0)
    spread = SPREADS.get(instrument, 0.01)
    volatility = VOLATILITY.get(instrument, 0.01)

    # Get bias from open positions (market tends to move toward TP)
    signal_bias = get_signal_bias(instrument)

    # Initialize or update trend (trends persist for a while)
    if instrument not in price_trend or random.random() < 0.1:  # 10% chance to change trend
        # Random trend direction, influenced by signal bias
        trend_base = random.gauss(signal_bias * 0.5, 0.3)
        price_trend[instrument] = max(-1, min(1, trend_base))  # Clamp to [-1, 1]

    # Price change = trend component + random noise
    trend = price_trend[instrument]
    trend_move = trend * volatility * 0.3  # Trend contributes 30%
    noise = random.gauss(0, volatility)     # Random noise

    change = trend_move + noise
    new_price = base + change
    current_prices[instrument] = new_price

    bid = new_price - spread / 2
    ask = new_price + spread / 2

    # Check and close trades that hit TP/SL
    check_tp_sl_hits(instrument, bid, ask)

    return bid, ask


def check_tp_sl_hits(instrument: str, bid: float, ask: float):
    """Check if any trades hit TP or SL"""
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            # Get open trades for this instrument with TP/SL
            # CRITICAL: Skip trades opened in last 10 seconds (grace period)
            cursor.execute("""
                SELECT * FROM paper_trades
                WHERE instrument = %s AND status = 'OPEN'
                AND (stop_loss IS NOT NULL OR take_profit IS NOT NULL)
                AND opened_at < NOW() - INTERVAL 10 SECOND
            """, (instrument,))
            trades = cursor.fetchall()

            for trade in trades:
                close_price = None
                close_reason = None
                current = bid if trade["side"] == "BUY" else ask

                # For BUY trades:
                #   - SL hit when price drops below SL (bid < SL)
                #   - TP hit when price rises above TP (bid > TP)
                # For SELL trades:
                #   - SL hit when price rises above SL (ask > SL)
                #   - TP hit when price drops below TP (ask < TP)

                if trade["side"] == "BUY":
                    if trade["stop_loss"] and bid <= float(trade["stop_loss"]):
                        close_price = float(trade["stop_loss"])
                        close_reason = "STOP_LOSS"
                    elif trade["take_profit"] and bid >= float(trade["take_profit"]):
                        close_price = float(trade["take_profit"])
                        close_reason = "TAKE_PROFIT"
                else:  # SELL
                    if trade["stop_loss"] and ask >= float(trade["stop_loss"]):
                        close_price = float(trade["stop_loss"])
                        close_reason = "STOP_LOSS"
                    elif trade["take_profit"] and ask <= float(trade["take_profit"]):
                        close_price = float(trade["take_profit"])
                        close_reason = "TAKE_PROFIT"

                if close_price and close_reason:
                    # Calculate P&L
                    if trade["side"] == "BUY":
                        pnl = (close_price - float(trade["open_price"])) * float(trade["units"])
                    else:
                        pnl = (float(trade["open_price"]) - close_price) * float(trade["units"])

                    # Close the trade
                    cursor.execute("""
                        UPDATE paper_trades
                        SET status = 'CLOSED', close_price = %s, close_time = NOW(),
                            realized_pnl = %s, close_reason = %s
                        WHERE trade_id = %s
                    """, (close_price, pnl, close_reason, trade["trade_id"]))

                    # Update account balance
                    cursor.execute("""
                        UPDATE paper_accounts
                        SET current_balance = current_balance + %s,
                            realized_pnl = realized_pnl + %s
                        WHERE account_id = %s
                    """, (pnl, pnl, trade["account_id"]))

                    print(f"[MOCK] {close_reason} HIT: {trade['trade_id']} @ {close_price:.5f} P&L: {pnl:.2f}")

            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[MOCK] TP/SL check error: {e}")


def generate_price_tick(instrument: str) -> dict:
    """Generate a price tick"""
    bid, ask = get_current_price(instrument)

    return {
        "type": "PRICE",
        "time": datetime.now(timezone.utc).isoformat(),
        "bids": [{"price": f"{bid:.5f}", "liquidity": 1000000}],
        "asks": [{"price": f"{ask:.5f}", "liquidity": 1000000}],
        "closeoutBid": f"{bid:.5f}",
        "closeoutAsk": f"{ask:.5f}",
        "status": "tradeable",
        "tradeable": True,
        "instrument": instrument,
    }


# =============================================================================
# Order Models
# =============================================================================
class OrderRequest(BaseModel):
    order: dict


# =============================================================================
# Streaming Endpoints
# =============================================================================
async def price_stream(instruments: list):
    """Generate continuous price stream"""
    try:
        while True:
            for instrument in instruments:
                price = generate_price_tick(instrument)
                data = json.dumps(price) + "\n"
                yield data.encode('utf-8')
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        raise


@app.get("/v3/accounts/{account_id}/pricing/stream")
async def stream_pricing(account_id: str, request: Request, instruments: str = "XAU_USD"):
    """Stream prices"""
    instrument_list = instruments.split(",")
    print(f"[MOCK] Streaming: {instrument_list}", flush=True)

    return StreamingResponse(
        price_stream(instrument_list),
        media_type="application/octet-stream",
        headers={"X-Mock": "true"}
    )


@app.get("/v3/accounts/{account_id}/pricing")
async def get_pricing(account_id: str, instruments: str = "XAU_USD"):
    """Get current prices"""
    instrument_list = instruments.split(",")
    prices = [generate_price_tick(inst) for inst in instrument_list]
    return {"time": datetime.now(timezone.utc).isoformat(), "prices": prices}


# =============================================================================
# Account Endpoints
# =============================================================================
@app.get("/v3/accounts")
async def list_accounts():
    """List accounts"""
    return {"accounts": [{"id": "MOCK-DEV-001", "tags": ["mock", "dev"]}]}


@app.get("/v3/accounts/{account_id}")
async def get_account(account_id: str):
    """Get account details with balance from DB - OANDA v20 compatible"""
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            # Get account
            cursor.execute(
                "SELECT * FROM paper_accounts WHERE account_id = %s",
                (account_id,)
            )
            account = cursor.fetchone()

            # Count open trades and positions
            cursor.execute(
                "SELECT COUNT(*) as count FROM paper_trades WHERE account_id = %s AND status = 'OPEN'",
                (account_id,)
            )
            open_trade_count = cursor.fetchone()["count"]

            cursor.execute(
                "SELECT COUNT(DISTINCT instrument) as count FROM paper_trades WHERE account_id = %s AND status = 'OPEN'",
                (account_id,)
            )
            open_position_count = cursor.fetchone()["count"]

            # Calculate unrealized P&L from open trades dynamically
            cursor.execute(
                "SELECT instrument, side, units, open_price FROM paper_trades WHERE account_id = %s AND status = 'OPEN'",
                (account_id,)
            )
            open_trades = cursor.fetchall()

        conn.close()

        # Calculate unrealized P&L from open trades using current (dynamic) prices
        unrealized_pnl = 0.0
        for trade in open_trades:
            instrument = trade["instrument"]
            # Use current_prices (dynamic) not BASE_PRICES (static)
            bid, ask = get_current_price(instrument)
            current_price = bid if trade["side"] == "BUY" else ask
            open_price = float(trade["open_price"])
            units = abs(float(trade["units"]))
            side = trade["side"]

            if side == "BUY":
                pnl = (current_price - open_price) * units
            else:
                pnl = (open_price - current_price) * units
            unrealized_pnl += pnl

        # Default values
        balance = float(account["current_balance"]) if account else 100000.0
        currency = account["currency"] if account else "GBP"
        realized_pnl = float(account["realized_pnl"]) if account else 0.0
        margin_used = float(account["margin_used"]) if account and account.get("margin_used") else 0.0

        nav = balance + unrealized_pnl
        margin_available = nav - margin_used

        # OANDA v20 compatible response
        return {
            "account": {
                "id": account_id,
                "alias": "Mock DEV Account",
                "currency": currency,
                "balance": f"{balance:.4f}",
                "createdByUserID": 1,
                "createdTime": "2026-01-01T00:00:00.000000Z",
                "pl": f"{realized_pnl:.4f}",
                "resettablePL": f"{realized_pnl:.4f}",
                "unrealizedPL": f"{unrealized_pnl:.4f}",
                "NAV": f"{nav:.4f}",
                "marginUsed": f"{margin_used:.4f}",
                "marginAvailable": f"{margin_available:.4f}",
                "marginRate": "0.02",
                "marginCloseoutUnrealizedPL": f"{unrealized_pnl:.4f}",
                "marginCloseoutNAV": f"{nav:.4f}",
                "marginCloseoutMarginUsed": f"{margin_used:.4f}",
                "marginCloseoutPercent": "0.0000",
                "marginCloseoutPositionValue": "0.0000",
                "withdrawalLimit": f"{margin_available:.4f}",
                "positionValue": "0.0000",
                "openTradeCount": open_trade_count,
                "openPositionCount": open_position_count,
                "pendingOrderCount": 0,
                "hedgingEnabled": False,
                "lastTransactionID": "1000"
            },
            "lastTransactionID": "1000"
        }
    except Exception as e:
        print(f"[MOCK] DB error: {e}")
        return {
            "account": {
                "id": account_id,
                "currency": "GBP",
                "balance": "100000.0000",
                "pl": "0.0000",
                "unrealizedPL": "0.0000",
                "NAV": "100000.0000",
                "openTradeCount": 0,
                "openPositionCount": 0
            },
            "lastTransactionID": "1000"
        }


@app.get("/v3/accounts/{account_id}/summary")
async def get_account_summary(account_id: str):
    """Get account summary - OANDA v20 compatible (same as account but lighter)"""
    return await get_account(account_id)


# =============================================================================
# Order Endpoints
# =============================================================================
@app.post("/v3/accounts/{account_id}/orders")
async def create_order(account_id: str, request: Request):
    """Create and fill a market order"""
    body = await request.json()
    order_data = body.get("order", {})

    instrument = order_data.get("instrument")
    units = float(order_data.get("units", 0))
    order_type = order_data.get("type", "MARKET")

    if not instrument or units == 0:
        raise HTTPException(400, "Invalid order: instrument and units required")

    # Get current price
    bid, ask = get_current_price(instrument)
    fill_price = ask if units > 0 else bid  # Buy at ask, sell at bid

    # Generate IDs
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    trade_id = f"TRD-{uuid.uuid4().hex[:8].upper()}"

    side = "BUY" if units > 0 else "SELL"
    abs_units = abs(units)

    # Extract SL/TP
    sl_price = None
    tp_price = None
    if "stopLossOnFill" in order_data:
        sl_price = float(order_data["stopLossOnFill"].get("price", 0))
    if "takeProfitOnFill" in order_data:
        tp_price = float(order_data["takeProfitOnFill"].get("price", 0))

    try:
        conn = get_db()
        with conn.cursor() as cursor:
            # Insert order
            cursor.execute("""
                INSERT INTO paper_orders
                (order_id, account_id, instrument, order_type, side, units,
                 stop_loss, take_profit, status, fill_price, fill_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'FILLED', %s, NOW())
            """, (order_id, account_id, instrument, order_type, side,
                  abs_units, sl_price, tp_price, fill_price))

            # Insert trade
            cursor.execute("""
                INSERT INTO paper_trades
                (trade_id, order_id, account_id, instrument, side, units,
                 open_price, current_price, stop_loss, take_profit, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
            """, (trade_id, order_id, account_id, instrument, side,
                  abs_units, fill_price, fill_price, sl_price, tp_price))

            conn.commit()
        conn.close()

        print(f"[MOCK] Order filled: {side} {abs_units} {instrument} @ {fill_price:.5f}")

        return {
            "orderCreateTransaction": {
                "id": order_id,
                "type": "MARKET_ORDER",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
            },
            "orderFillTransaction": {
                "id": order_id,
                "tradeOpened": {
                    "tradeID": trade_id,
                    "units": str(abs_units),
                    "price": f"{fill_price:.5f}",
                },
                "pl": "0.0000",
                "financing": "0.0000",
            },
            "relatedTransactionIDs": [order_id],
        }

    except Exception as e:
        print(f"[MOCK] Order error: {e}")
        raise HTTPException(500, f"Order failed: {e}")


# =============================================================================
# Trade Endpoints
# =============================================================================
@app.get("/v3/accounts/{account_id}/trades")
async def get_trades(account_id: str, state: str = "ALL", count: int = 50):
    """Get trades filtered by state (OPEN, CLOSED, ALL). Default ALL to match OANDA v20."""
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            if state.upper() == "OPEN":
                cursor.execute("""
                    SELECT * FROM paper_trades
                    WHERE account_id = %s AND status = 'OPEN'
                    ORDER BY opened_at DESC LIMIT %s
                """, (account_id, count))
            elif state.upper() == "CLOSED":
                cursor.execute("""
                    SELECT * FROM paper_trades
                    WHERE account_id = %s AND status = 'CLOSED'
                    ORDER BY close_time DESC LIMIT %s
                """, (account_id, count))
            else:  # ALL
                cursor.execute("""
                    SELECT * FROM paper_trades
                    WHERE account_id = %s
                    ORDER BY opened_at DESC LIMIT %s
                """, (account_id, count))
            trades = cursor.fetchall()
        conn.close()

        result = []
        for trade in trades:
            units_signed = float(trade["units"]) if trade["side"] == "BUY" else -float(trade["units"])

            if trade["status"] == "OPEN":
                bid, ask = get_current_price(trade["instrument"])
                current = bid if trade["side"] == "BUY" else ask

                # Calculate unrealized P&L
                if trade["side"] == "BUY":
                    pnl = (current - float(trade["open_price"])) * float(trade["units"])
                else:
                    pnl = (float(trade["open_price"]) - current) * float(trade["units"])

                # OANDA v20 compatible Trade object
                result.append({
                    "id": trade["trade_id"],
                    "instrument": trade["instrument"],
                    "price": f"{float(trade['open_price']):.5f}",
                    "openTime": trade["opened_at"].isoformat() + "Z" if trade["opened_at"] else None,
                    "initialUnits": f"{units_signed:.0f}",
                    "currentUnits": f"{units_signed:.0f}",
                    "realizedPL": "0.0000",
                    "unrealizedPL": f"{pnl:.4f}",
                    "financing": "0.0000",
                    "state": "OPEN",
                    "takeProfitOrder": {
                        "price": f"{float(trade['take_profit']):.5f}"
                    } if trade.get("take_profit") else None,
                    "stopLossOrder": {
                        "price": f"{float(trade['stop_loss']):.5f}"
                    } if trade.get("stop_loss") else None,
                })
            else:  # CLOSED
                result.append({
                    "id": trade["trade_id"],
                    "instrument": trade["instrument"],
                    "price": f"{float(trade['open_price']):.5f}",
                    "openTime": trade["opened_at"].isoformat() + "Z" if trade["opened_at"] else None,
                    "closeTime": trade["close_time"].isoformat() + "Z" if trade["close_time"] else None,
                    "initialUnits": f"{units_signed:.0f}",
                    "currentUnits": "0",
                    "realizedPL": f"{float(trade['realized_pnl']):.4f}" if trade["realized_pnl"] else "0.0000",
                    "unrealizedPL": "0.0000",
                    "financing": "0.0000",
                    "closePrice": f"{float(trade['close_price']):.5f}" if trade["close_price"] else None,
                    "closeReason": trade["close_reason"],
                    "state": "CLOSED",
                })

        return {
            "trades": result,
            "lastTransactionID": "1000"
        }

    except Exception as e:
        print(f"[MOCK] Trades error: {e}")
        return {"trades": []}


@app.get("/v3/accounts/{account_id}/openTrades")
async def get_open_trades(account_id: str):
    """Get open trades only (convenience endpoint)"""
    return await get_trades(account_id, state="OPEN")




@app.put("/v3/accounts/{account_id}/trades/{trade_id}/close")
async def close_trade(account_id: str, trade_id: str):
    """Close a specific trade"""
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            # Get trade
            cursor.execute(
                "SELECT * FROM paper_trades WHERE trade_id = %s AND status = 'OPEN'",
                (trade_id,)
            )
            trade = cursor.fetchone()

            if not trade:
                raise HTTPException(404, f"Trade {trade_id} not found")

            # Get close price
            bid, ask = get_current_price(trade["instrument"])
            close_price = bid if trade["side"] == "BUY" else ask

            # Calculate P&L
            if trade["side"] == "BUY":
                pnl = (close_price - float(trade["open_price"])) * float(trade["units"])
            else:
                pnl = (float(trade["open_price"]) - close_price) * float(trade["units"])

            # Close trade
            cursor.execute("""
                UPDATE paper_trades
                SET status = 'CLOSED', close_price = %s, close_time = NOW(),
                    realized_pnl = %s, close_reason = 'MANUAL'
                WHERE trade_id = %s
            """, (close_price, pnl, trade_id))

            # Update account balance
            cursor.execute("""
                UPDATE paper_accounts
                SET current_balance = current_balance + %s,
                    realized_pnl = realized_pnl + %s
                WHERE account_id = %s
            """, (pnl, pnl, account_id))

            conn.commit()
        conn.close()

        print(f"[MOCK] Trade closed: {trade_id} P&L: {pnl:.2f}")

        return {
            "orderFillTransaction": {
                "id": f"CLOSE-{trade_id}",
                "tradesClosed": [{
                    "tradeID": trade_id,
                    "units": str(trade["units"]),
                    "price": f"{close_price:.5f}",
                    "realizedPL": f"{pnl:.4f}",
                }],
                "pl": f"{pnl:.4f}",
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[MOCK] Close error: {e}")
        raise HTTPException(500, f"Close failed: {e}")


# =============================================================================
# Position Endpoints
# =============================================================================
@app.get("/v3/accounts/{account_id}/openPositions")
async def get_open_positions(account_id: str):
    """Get aggregated open positions"""
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT instrument, side, SUM(units) as total_units,
                       AVG(open_price) as avg_price
                FROM paper_trades
                WHERE account_id = %s AND status = 'OPEN'
                GROUP BY instrument, side
            """, (account_id,))
            positions = cursor.fetchall()
        conn.close()

        result = []
        for pos in positions:
            bid, ask = get_current_price(pos["instrument"])
            current = bid if pos["side"] == "BUY" else ask

            if pos["side"] == "BUY":
                pnl = (current - float(pos["avg_price"])) * float(pos["total_units"])
                units = float(pos["total_units"])
            else:
                pnl = (float(pos["avg_price"]) - current) * float(pos["total_units"])
                units = -float(pos["total_units"])

            result.append({
                "instrument": pos["instrument"],
                "long": {
                    "units": str(pos["total_units"]) if pos["side"] == "BUY" else "0",
                    "averagePrice": str(pos["avg_price"]) if pos["side"] == "BUY" else None,
                    "pl": f"{pnl:.4f}" if pos["side"] == "BUY" else "0.0000",
                    "unrealizedPL": f"{pnl:.4f}" if pos["side"] == "BUY" else "0.0000",
                },
                "short": {
                    "units": str(pos["total_units"]) if pos["side"] == "SELL" else "0",
                    "averagePrice": str(pos["avg_price"]) if pos["side"] == "SELL" else None,
                    "pl": f"{pnl:.4f}" if pos["side"] == "SELL" else "0.0000",
                    "unrealizedPL": f"{pnl:.4f}" if pos["side"] == "SELL" else "0.0000",
                },
                "unrealizedPL": f"{pnl:.4f}",
            })

        return {"positions": result}

    except Exception as e:
        print(f"[MOCK] Positions error: {e}")
        return {"positions": []}


@app.get("/v3/accounts/{account_id}/positions")
async def get_positions(account_id: str):
    """Get all positions (same as openPositions for mock)"""
    return await get_open_positions(account_id)


@app.put("/v3/accounts/{account_id}/positions/{instrument}/close")
async def close_position(account_id: str, instrument: str, request: Request):
    """Close all trades for an instrument"""
    body = await request.json()

    try:
        conn = get_db()
        with conn.cursor() as cursor:
            # Get all open trades for this instrument
            cursor.execute("""
                SELECT * FROM paper_trades
                WHERE account_id = %s AND instrument = %s AND status = 'OPEN'
            """, (account_id, instrument))
            trades = cursor.fetchall()

            if not trades:
                raise HTTPException(404, f"No open position for {instrument}")

            total_pnl = 0
            bid, ask = get_current_price(instrument)

            for trade in trades:
                close_price = bid if trade["side"] == "BUY" else ask

                if trade["side"] == "BUY":
                    pnl = (close_price - float(trade["open_price"])) * float(trade["units"])
                else:
                    pnl = (float(trade["open_price"]) - close_price) * float(trade["units"])

                total_pnl += pnl

                cursor.execute("""
                    UPDATE paper_trades
                    SET status = 'CLOSED', close_price = %s, close_time = NOW(),
                        realized_pnl = %s, close_reason = 'MANUAL'
                    WHERE trade_id = %s
                """, (close_price, pnl, trade["trade_id"]))

            # Update account
            cursor.execute("""
                UPDATE paper_accounts
                SET current_balance = current_balance + %s,
                    realized_pnl = realized_pnl + %s
                WHERE account_id = %s
            """, (total_pnl, total_pnl, account_id))

            conn.commit()
        conn.close()

        print(f"[MOCK] Position closed: {instrument} P&L: {total_pnl:.2f}")

        return {
            "longOrderFillTransaction": {
                "pl": f"{total_pnl:.4f}",
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[MOCK] Close position error: {e}")
        raise HTTPException(500, f"Close failed: {e}")


# =============================================================================
# Transaction Endpoints (Ledger)
# =============================================================================
@app.get("/v3/accounts/{account_id}/transactions")
async def get_transactions(account_id: str, count: int = 50, type: str = None):
    """Get transaction history - OANDA v20 compatible"""
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM paper_transactions
                WHERE account_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (account_id, count))
            transactions = cursor.fetchall()
        conn.close()

        result = []
        for txn in transactions:
            result.append({
                "id": str(txn["id"]),
                "time": txn["created_at"].isoformat() + "Z" if txn["created_at"] else None,
                "type": txn["transaction_type"],
                "accountID": account_id,
                "amount": f"{float(txn['amount']):.4f}",
                "accountBalance": f"{float(txn['balance_after']):.4f}",
                "reason": txn.get("description", ""),
            })

        return {
            "transactions": result,
            "lastTransactionID": str(result[0]["id"]) if result else "0",
            "count": len(result)
        }

    except Exception as e:
        print(f"[MOCK] Transactions error: {e}")
        return {"transactions": [], "lastTransactionID": "0", "count": 0}


# =============================================================================
# Health & Info
# =============================================================================
@app.get("/health")
async def health():
    return {"status": "ok", "mock": True, "trading": True}


@app.get("/")
async def root():
    return {
        "name": "Mock OANDA Trading API",
        "version": "2.0.0",
        "endpoints": [
            "GET  /v3/accounts",
            "GET  /v3/accounts/{id}",
            "GET  /v3/accounts/{id}/pricing",
            "GET  /v3/accounts/{id}/pricing/stream",
            "POST /v3/accounts/{id}/orders",
            "GET  /v3/accounts/{id}/trades",
            "PUT  /v3/accounts/{id}/trades/{trade_id}/close",
            "GET  /v3/accounts/{id}/openPositions",
            "PUT  /v3/accounts/{id}/positions/{instrument}/close",
        ]
    }


if __name__ == "__main__":
    print("=" * 60)
    print("MOCK OANDA TRADING API v2.0")
    print("=" * 60)
    print("Endpoint: http://localhost:8299")
    print("Database: tradingPaper")
    print("=" * 60)
    print("Supported operations:")
    print("  - Price streaming")
    print("  - Order placement")
    print("  - Trade management")
    print("  - Position tracking")
    print("  - P&L calculation")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8299)
