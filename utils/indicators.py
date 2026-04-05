#!/usr/bin/env python3
"""
Technical Indicators
EPIC-108: Signal Processor Service

RSI and EMA calculations ported from trading-kernel.
EMA pairs are configurable via trading_config table.
"""

from typing import List, Tuple, Optional


# Default EMA pairs (used if config not loaded)
DEFAULT_EMA_PAIRS = [
    (9, 21),   # Fast scalping
    (12, 26),  # MACD-style
    (20, 50),  # Swing trading
]

# Module-level EMA pairs (can be updated from config)
EMA_PAIRS = DEFAULT_EMA_PAIRS.copy()


def set_ema_pairs(pairs: List[Tuple[int, int]]) -> None:
    """
    Set EMA pairs from config.

    Called by SignalProcessor after loading config from database.

    Args:
        pairs: List of (fast, slow) tuples
    """
    global EMA_PAIRS
    if pairs:
        EMA_PAIRS = pairs


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculate Relative Strength Index.

    Args:
        prices: List of closing prices (oldest first)
        period: RSI period (default 14)

    Returns:
        RSI value (0-100)
    """
    if len(prices) < period + 1:
        return 50.0  # Neutral if insufficient data

    # Calculate price deltas
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]

    # Separate gains and losses
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]

    # Simple averages
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # RSI formula: 100 - (100 / (1 + RS))
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_ema(prices: List[float], period: int) -> float:
    """
    Calculate Exponential Moving Average.

    Args:
        prices: List of closing prices (oldest first)
        period: EMA period

    Returns:
        EMA value
    """
    if len(prices) < period:
        return prices[-1] if prices else 0.0

    # Multiplier for smoothing
    multiplier = 2 / (period + 1)

    # Initialize with simple moving average
    ema = sum(prices[:period]) / period

    # Apply EMA formula to remaining prices
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def get_ema_state(fast: float, slow: float) -> str:
    """
    Determine EMA crossover state.

    Args:
        fast: Fast EMA value
        slow: Slow EMA value

    Returns:
        'above' if fast > slow, 'below' if fast < slow, 'cross' if equal
    """
    if fast > slow:
        return 'above'
    elif fast < slow:
        return 'below'
    else:
        return 'cross'


def calculate_ema_pair(prices: List[float], fast_period: int, slow_period: int) -> dict:
    """
    Calculate an EMA pair with state.

    Args:
        prices: List of closing prices
        fast_period: Fast EMA period
        slow_period: Slow EMA period

    Returns:
        Dict with fast, slow, and state
    """
    fast = calculate_ema(prices, fast_period)
    slow = calculate_ema(prices, slow_period)
    state = get_ema_state(fast, slow)

    return {
        'fast': round(fast, 5),
        'slow': round(slow, 5),
        'state': state
    }


def calculate_all_indicators(prices: List[float], ema_pairs: Optional[List[Tuple[int, int]]] = None) -> dict:
    """
    Calculate all indicators for a signal.

    Args:
        prices: List of closing prices (oldest first)
        ema_pairs: Optional list of (fast, slow) tuples. Uses module EMA_PAIRS if not specified.

    Returns:
        Dict with all indicator values
    """
    # Use provided pairs or fall back to module-level config
    pairs_to_use = ema_pairs if ema_pairs is not None else EMA_PAIRS

    result = {
        'rsi_14': round(calculate_rsi(prices, 14), 2),
        'ema': {}
    }

    for fast_period, slow_period in pairs_to_use:
        key = f"{fast_period}_{slow_period}"
        result['ema'][key] = calculate_ema_pair(prices, fast_period, slow_period)

    return result
