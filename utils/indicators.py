#!/usr/bin/env python3
"""
Technical Indicators
EPIC-108: Signal Processor Service
EPIC-D026: HERMES SSOT Upgrade (ADR-077)

RSI and EMA calculations ported from trading-kernel.
EMA pairs are configurable via trading_config table.

EPIC-D026 Additions:
- ADX (Average Directional Index) with +DI/-DI
- Bollinger Bands with squeeze detection
"""

from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass


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


# =============================================================================
# EPIC-D026: ADX Calculation (Average Directional Index)
# =============================================================================

@dataclass
class ADXResult:
    """Result of ADX calculation."""
    adx: float
    plus_di: float
    minus_di: float


def calculate_true_range(high: float, low: float, prev_close: float) -> float:
    """
    Calculate True Range for a single bar.

    TR = max(high - low, |high - prev_close|, |low - prev_close|)
    """
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close)
    )


def calculate_adx(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> ADXResult:
    """
    Calculate Average Directional Index with +DI and -DI.

    Uses Wilder smoothing (not simple moving average).

    Args:
        highs: List of high prices (oldest first)
        lows: List of low prices (oldest first)
        closes: List of close prices (oldest first)
        period: ADX period (default 14)

    Returns:
        ADXResult with adx, plus_di, minus_di

    Algorithm:
        1. Calculate +DM and -DM (Directional Movement)
        2. Calculate True Range (TR)
        3. Apply Wilder smoothing to get smoothed +DM, -DM, TR
        4. Calculate +DI = 100 * smoothed_plus_dm / smoothed_tr
        5. Calculate -DI = 100 * smoothed_minus_dm / smoothed_tr
        6. Calculate DX = 100 * |+DI - -DI| / (+DI + -DI)
        7. ADX = Wilder smoothed DX
    """
    n = len(closes)
    if n < period + 1:
        return ADXResult(adx=20.0, plus_di=20.0, minus_di=20.0)  # Neutral

    # Calculate directional movements and true ranges
    plus_dm_list = []
    minus_dm_list = []
    tr_list = []

    for i in range(1, n):
        # Directional Movement
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]

        # +DM: Up move is positive and greater than down move
        if up_move > down_move and up_move > 0:
            plus_dm = up_move
        else:
            plus_dm = 0

        # -DM: Down move is positive and greater than up move
        if down_move > up_move and down_move > 0:
            minus_dm = down_move
        else:
            minus_dm = 0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

        # True Range
        tr = calculate_true_range(highs[i], lows[i], closes[i-1])
        tr_list.append(tr)

    if len(tr_list) < period:
        return ADXResult(adx=20.0, plus_di=20.0, minus_di=20.0)

    # Wilder smoothing: first value is sum, then smooth = prev - (prev/period) + current
    # Initialize with first 'period' values
    smoothed_plus_dm = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])
    smoothed_tr = sum(tr_list[:period])

    # Apply Wilder smoothing for remaining values
    dx_list = []

    for i in range(period, len(tr_list)):
        # Wilder smoothing
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]

        # Calculate +DI and -DI
        if smoothed_tr > 0:
            plus_di = 100 * smoothed_plus_dm / smoothed_tr
            minus_di = 100 * smoothed_minus_dm / smoothed_tr
        else:
            plus_di = 0
            minus_di = 0

        # Calculate DX
        di_sum = plus_di + minus_di
        if di_sum > 0:
            dx = 100 * abs(plus_di - minus_di) / di_sum
        else:
            dx = 0

        dx_list.append(dx)

    if len(dx_list) < period:
        # Not enough data for full ADX smoothing
        if dx_list:
            return ADXResult(
                adx=round(sum(dx_list) / len(dx_list), 2),
                plus_di=round(plus_di, 2),
                minus_di=round(minus_di, 2)
            )
        return ADXResult(adx=20.0, plus_di=20.0, minus_di=20.0)

    # Wilder smooth DX to get ADX
    adx = sum(dx_list[:period]) / period  # Initial ADX

    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period

    return ADXResult(
        adx=round(adx, 2),
        plus_di=round(plus_di, 2),
        minus_di=round(minus_di, 2)
    )


# =============================================================================
# EPIC-D026: Bollinger Bands Calculation
# =============================================================================

@dataclass
class BollingerBandsResult:
    """Result of Bollinger Bands calculation."""
    middle: float
    upper: float
    lower: float
    width_pct: float
    squeeze: bool


def calculate_bollinger_bands(
    prices: List[float],
    period: int = 20,
    std_dev: float = 2.0,
    squeeze_threshold: float = 2.0
) -> BollingerBandsResult:
    """
    Calculate Bollinger Bands with squeeze detection.

    Args:
        prices: List of closing prices (oldest first)
        period: BB period (default 20)
        std_dev: Number of standard deviations (default 2.0)
        squeeze_threshold: Width % below this = squeeze (default 2.0)

    Returns:
        BollingerBandsResult with middle, upper, lower, width_pct, squeeze

    Formula:
        Middle Band = 20-period SMA
        Upper Band = Middle + (std_dev * 20-period StdDev)
        Lower Band = Middle - (std_dev * 20-period StdDev)
        Width % = (Upper - Lower) / Middle * 100
        Squeeze = Width % < squeeze_threshold
    """
    if len(prices) < period:
        # Not enough data
        current = prices[-1] if prices else 0
        return BollingerBandsResult(
            middle=current,
            upper=current,
            lower=current,
            width_pct=0.0,
            squeeze=False
        )

    # Use most recent 'period' prices
    recent = prices[-period:]

    # Middle band = SMA
    middle = sum(recent) / period

    # Standard deviation
    variance = sum((p - middle) ** 2 for p in recent) / period
    std = variance ** 0.5

    # Upper and lower bands
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)

    # Width as percentage
    if middle > 0:
        width_pct = ((upper - lower) / middle) * 100
    else:
        width_pct = 0.0

    # Squeeze detection
    squeeze = width_pct < squeeze_threshold

    return BollingerBandsResult(
        middle=round(middle, 5),
        upper=round(upper, 5),
        lower=round(lower, 5),
        width_pct=round(width_pct, 4),
        squeeze=squeeze
    )
