# src/gbot/analysis/backtester.py
"""
Grid Trading Backtester.

Simuliert den Grid-Bot auf historischen OHLCV-Daten.
Wird vom Optimizer verwendet um verschiedene (num_grids, leverage)-Kombinationen
zu vergleichen.

Ablauf:
  1. Grid-Levels berechnen (lower, upper, num_grids → N+1 Preisstufen)
  2. Initiale Orders: Buy unterhalb, Sell oberhalb des Startpreises
  3. Pro Kerze: Fills erkennen, Nachfolge-Orders platzieren
  4. PnL, Drawdown, Fill-Anzahl tracken

Gewinn-Modell (Futures neutral grid):
  - Buy-Fill bei Preis P:  -fee (fee = P * amount * fee_rate)
  - Sell-Fill bei Preis P: +spacing * amount - fee  (Profit = Abstand * Menge)
  - Netto pro Buy→Sell-Zyklus: spacing * amount - 2 * fees
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

MIN_NOTIONAL_USDT = 5.0
DEFAULT_FEE_RATE = 0.0006   # 0.06 % Taker-Gebühr (Bitget Futures)
LEVEL_DECIMALS = 6           # Rundungsgenauigkeit für Level-Vergleiche


def _r(price: float) -> float:
    """Runde Preis auf LEVEL_DECIMALS Stellen (verhindert Float-Vergleichs-Fehler)."""
    return round(price, LEVEL_DECIMALS)


# ---------------------------------------------------------------------------
# Haupt-Backtest-Funktion
# ---------------------------------------------------------------------------

def run_grid_backtest(
    df: pd.DataFrame,
    lower: float,
    upper: float,
    num_grids: int,
    leverage: float,
    capital: float,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> dict:
    """
    Simuliert Grid-Trading auf OHLCV-Daten.

    Args:
        df         : OHLCV DataFrame (Spalten: open, high, low, close)
        lower      : untere Grid-Grenze
        upper      : obere Grid-Grenze
        num_grids  : Anzahl Grid-Stufen (Anzahl Levels = num_grids + 1)
        leverage   : Hebel-Faktor
        capital    : Startkapital in USDT
        fee_rate   : Taker-Gebühr pro Order (Standard 0.06 %)

    Returns:
        dict mit roi_pct, max_drawdown_pct, total_fills, total_pnl_usdt,
        spacing, amount_per_grid — oder Fehlerfeld 'error'
    """
    # --- Validierung ---
    if upper <= lower:
        return _error('upper <= lower')
    if num_grids < 2:
        return _error('num_grids < 2')
    if capital <= 0:
        return _error('capital <= 0')
    if leverage < 1:
        return _error('leverage < 1')
    if len(df) < 2:
        return _error('zu wenige Kerzen')

    # --- Grid-Levels ---
    spacing = (upper - lower) / num_grids
    levels = [_r(lower + i * spacing) for i in range(num_grids + 1)]

    # --- Menge pro Grid-Stufe ---
    mid_price = (upper + lower) / 2.0
    amount = (capital * leverage) / (num_grids * mid_price)

    if amount * mid_price < MIN_NOTIONAL_USDT:
        return _error(f'min_notional: {amount * mid_price:.2f} < {MIN_NOTIONAL_USDT}')

    # --- Initiale Orders ---
    init_price = float(df['close'].iloc[0])
    buy_orders: set[float] = {l for l in levels if l < init_price}
    sell_orders: set[float] = {l for l in levels if l > init_price}

    total_pnl = 0.0
    total_fills = 0
    buy_fills = 0
    sell_fills = 0
    peak_capital = capital
    max_drawdown_pct = 0.0
    grid_restarts = 0
    # open_positions:  sell_level -> buy_price  (offene Longs)
    # short_positions: sell_level -> sell_price  (offene Shorts)
    open_positions: dict = {}
    short_positions: dict = {}

    half_range = (upper - lower) / 2.0

    # --- Kerzen durchlaufen ---
    for _, row in df.iterrows():
        candle_low = float(row['low'])
        candle_high = float(row['high'])
        close_price = float(row['close'])

        new_sell_orders: set[float] = set()
        new_buy_orders: set[float] = set()

        # Buy-Fills
        for bp in list(buy_orders):
            if candle_low <= bp:
                sp_key = _r(bp + spacing)
                closed_short = short_positions.pop(sp_key, None)
                if closed_short is not None:
                    # Short schliessen: Gewinn = spacing * amount - Fee
                    total_pnl += spacing * amount - bp * amount * fee_rate
                else:
                    # Long eroeffnen: nur Entry-Fee
                    total_pnl -= bp * amount * fee_rate
                    if sp_key <= _r(upper) + 1e-9:
                        open_positions[sp_key] = bp
                buy_orders.discard(bp)
                buy_fills += 1
                total_fills += 1
                sp = _r(bp + spacing)
                if sp <= _r(upper) + 1e-9:
                    new_sell_orders.add(sp)

        # Sell-Fills
        for sp in list(sell_orders):
            if candle_high >= sp:
                closed_long = open_positions.pop(sp, None)
                if closed_long is not None:
                    # Long schliessen: Gewinn = spacing * amount - Fee
                    total_pnl += spacing * amount - sp * amount * fee_rate
                else:
                    # Short eroeffnen: nur Entry-Fee
                    total_pnl -= sp * amount * fee_rate
                    short_positions[sp] = sp
                sell_orders.discard(sp)
                sell_fills += 1
                total_fills += 1
                bp = _r(sp - spacing)
                if bp >= _r(lower) - 1e-9:
                    new_buy_orders.add(bp)

        # Neue Orders hinzufügen (keine Duplikate)
        sell_orders.update(new_sell_orders - sell_orders)
        buy_orders.update(new_buy_orders - buy_orders)

        # Grid-SL: Cron prueft close_price — simuliert 15min-Cron
        def _restart_grid():
            nonlocal lower, upper, spacing, levels
            open_positions.clear()
            short_positions.clear()
            buy_orders.clear()
            sell_orders.clear()
            lower = _r(close_price - half_range)
            upper = _r(close_price + half_range)
            spacing = (upper - lower) / num_grids
            levels = [_r(lower + i * spacing) for i in range(num_grids + 1)]
            buy_orders.update(l for l in levels if l < close_price)
            sell_orders.update(l for l in levels if l > close_price)

        if close_price < lower:
            for bp in open_positions.values():
                total_pnl += (close_price - bp) * amount - close_price * amount * fee_rate
            for sp in short_positions:
                total_pnl += (sp - close_price) * amount - close_price * amount * fee_rate
            grid_restarts += 1
            _restart_grid()
        elif close_price > upper:
            for sp in short_positions:
                total_pnl += (sp - close_price) * amount - close_price * amount * fee_rate
            for bp in open_positions.values():
                total_pnl += (close_price - bp) * amount - close_price * amount * fee_rate
            grid_restarts += 1
            _restart_grid()

        # Drawdown: unrealisierte Verluste Longs + Shorts
        unrealized = (
            sum((close_price - bp) * amount for bp in open_positions.values()) +
            sum((sp - close_price) * amount for sp in short_positions)
        )
        current_capital = capital + total_pnl + unrealized
        if current_capital > peak_capital:
            peak_capital = current_capital
        if peak_capital > 0:
            dd = (peak_capital - current_capital) / peak_capital * 100.0
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

    roi_pct = total_pnl / capital * 100.0

    return {
        'roi_pct': round(roi_pct, 4),
        'max_drawdown_pct': round(max_drawdown_pct, 4),
        'total_pnl_usdt': round(total_pnl, 4),
        'total_fills': total_fills,
        'buy_fills': buy_fills,
        'sell_fills': sell_fills,
        'grid_restarts': grid_restarts,
        'num_grids': num_grids,
        'leverage': leverage,
        'spacing': round(spacing, 8),
        'amount_per_grid': round(amount, 8),
        'lower': lower,
        'upper': upper,
    }


def _error(msg: str) -> dict:
    return {
        'roi_pct': -9999.0,
        'max_drawdown_pct': 9999.0,
        'total_pnl_usdt': -9999.0,
        'total_fills': 0,
        'error': msg,
    }
