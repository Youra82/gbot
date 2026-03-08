# src/gbot/strategy/grid_logic.py
"""
Grid-Berechnungs-Logik fuer den gbot.

Grundprinzip (Long Grid auf Futures):
  - Der Preisbereich [lower_price, upper_price] wird in num_grids gleichmaessige
    Stufen unterteilt.
  - Unterhalb des aktuellen Preises werden Kauf-Limit-Orders platziert.
  - Fuellt sich eine Kauf-Order bei Preis P, wird sofort eine Verkauf-Order
    eine Stufe hoeher (P + spacing) platziert.
  - Fuellt sich eine Verkauf-Order, wird eine Kauf-Order eine Stufe tiefer
    (P - spacing) platziert.
  - Profit pro Zyklus = spacing * amount_per_grid.

Modi:
  - "long"  : nur Kauf-Orders im Bereich → profitiert von Range / leichtem Aufwaertstrend
  - "short" : nur Verkauf-Orders im Bereich → profitiert von Range / leichtem Abwaertstrend
  - "neutral": gemischt (Kauf unter, Verkauf ueber aktuellem Preis) → profitiert von Seitwarts
"""

import logging
import math
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


def calculate_grid_levels(lower_price: float, upper_price: float, num_grids: int) -> List[float]:
    """
    Berechnet gleichmaessig verteilte Grid-Preisstufen.

    Returns:
        Liste mit (num_grids + 1) Preisstufen, von unten nach oben.
    """
    if lower_price >= upper_price:
        raise ValueError(f"lower_price ({lower_price}) muss kleiner als upper_price ({upper_price}) sein.")
    if num_grids < 2:
        raise ValueError("num_grids muss mindestens 2 sein.")

    spacing = (upper_price - lower_price) / num_grids
    levels = [lower_price + i * spacing for i in range(num_grids + 1)]
    return levels


def get_grid_spacing(lower_price: float, upper_price: float, num_grids: int) -> float:
    """Abstand zwischen zwei Grid-Stufen."""
    return (upper_price - lower_price) / num_grids


def calculate_amount_per_grid(
    total_investment_usdt: float,
    num_grids: int,
    reference_price: float,
    leverage: int = 1,
) -> float:
    """
    Berechnet die Menge (in Coin-Einheiten) pro Grid-Stufe.

    total_investment_usdt: Gesamtes USDT-Kapital fuer das Grid
    num_grids: Anzahl Grid-Zellen
    reference_price: Referenzpreis fuer die Umrechnung (aktueller Kurs)
    leverage: Hebelfaktor (erhoehe effektives Kapital)

    Returns:
        Menge pro Grid-Stufe in Coin-Einheiten
    """
    usdt_per_grid = total_investment_usdt / num_grids
    coin_amount = (usdt_per_grid * leverage) / reference_price
    return coin_amount


def split_levels_by_price(
    levels: List[float],
    current_price: float,
    mode: str = 'neutral',
) -> Tuple[List[float], List[float]]:
    """
    Teilt die Grid-Stufen in Kauf- und Verkauf-Levels auf.

    Bei Modus 'neutral':
      - Kauf-Levels: alle Stufen < current_price (ohne die hoechste)
      - Verkauf-Levels: alle Stufen > current_price (ohne die niedrigste)

    Bei Modus 'long':
      - Alle Stufen ausser der obersten als Kauf-Levels
      - Keine initialen Verkauf-Levels (kommen erst nach einem Kauf-Fill)

    Bei Modus 'short':
      - Alle Stufen ausser der untersten als Verkauf-Levels
      - Keine initialen Kauf-Levels

    Returns:
        (buy_levels, sell_levels)
    """
    mode = mode.lower()

    if mode == 'neutral':
        buy_levels = [p for p in levels[:-1] if p < current_price]
        sell_levels = [p for p in levels[1:] if p > current_price]
        return buy_levels, sell_levels

    elif mode == 'long':
        buy_levels = levels[:-1]  # alle ausser oberster
        sell_levels = []
        return buy_levels, sell_levels

    elif mode == 'short':
        buy_levels = []
        sell_levels = levels[1:]  # alle ausser unterster
        return buy_levels, sell_levels

    else:
        raise ValueError(f"Unbekannter Grid-Modus: {mode}. Erlaubt: 'neutral', 'long', 'short'.")


def profit_per_cycle(
    grid_spacing: float,
    amount_per_grid: float,
    fee_pct: float = 0.06,
) -> float:
    """
    Schaetzung des Gewinns pro abgeschlossenem Kauf+Verkauf-Zyklus (ohne Hebelwirkung).

    grid_spacing: Preisabstand pro Stufe
    amount_per_grid: Menge in Coin-Einheiten
    fee_pct: Gebuehr in Prozent (ein-/ausgehend, z.B. 0.06 = 0.06%)

    Returns:
        Nettogewinn pro Zyklus in USDT (kann negativ sein wenn spacing zu klein)
    """
    gross_profit = grid_spacing * amount_per_grid
    # Gebuehren fuer Kauf und Verkauf
    # Vereinfacht: fee auf Transaktionsvolumen (Preis * Menge)
    fee_cost = 2 * (fee_pct / 100) * amount_per_grid * grid_spacing
    return gross_profit - fee_cost


def estimate_grid_roi(
    lower_price: float,
    upper_price: float,
    num_grids: int,
    total_investment_usdt: float,
    leverage: int,
    cycles_per_day: float = 5.0,
    fee_pct: float = 0.06,
) -> dict:
    """
    Schaetzung der Grid-Performance (informativ, nicht fuer Live-Trading genutzt).

    Returns:
        dict mit spacing, amount_per_grid, profit_per_cycle, daily_roi_pct
    """
    levels = calculate_grid_levels(lower_price, upper_price, num_grids)
    spacing = get_grid_spacing(lower_price, upper_price, num_grids)
    mid_price = (lower_price + upper_price) / 2
    amount = calculate_amount_per_grid(total_investment_usdt, num_grids, mid_price, leverage)
    ppc = profit_per_cycle(spacing, amount, fee_pct)
    daily_profit = ppc * cycles_per_day * num_grids
    daily_roi = (daily_profit / total_investment_usdt) * 100

    return {
        'num_levels': len(levels),
        'spacing': round(spacing, 6),
        'amount_per_grid': round(amount, 8),
        'profit_per_cycle_usdt': round(ppc, 4),
        'daily_roi_pct_estimate': round(daily_roi, 4),
    }


def find_next_buy_level(filled_sell_price: float, levels: List[float]) -> Optional[float]:
    """
    Nach einem Sell-Fill: naechste Kauf-Stufe einen Schritt tiefer bestimmen.
    Gibt None zurueck, wenn keine gueltige Stufe existiert.
    """
    candidates = [p for p in levels if p < filled_sell_price - 1e-8]
    if not candidates:
        return None
    return max(candidates)


def find_next_sell_level(filled_buy_price: float, levels: List[float]) -> Optional[float]:
    """
    Nach einem Buy-Fill: naechste Verkauf-Stufe einen Schritt hoeher bestimmen.
    Gibt None zurueck, wenn keine gueltige Stufe existiert.
    """
    candidates = [p for p in levels if p > filled_buy_price + 1e-8]
    if not candidates:
        return None
    return min(candidates)


def price_in_range(price: float, lower: float, upper: float) -> bool:
    """Prueft ob ein Preis innerhalb des Grid-Bereichs liegt."""
    return lower <= price <= upper


def format_grid_summary(
    symbol: str,
    lower_price: float,
    upper_price: float,
    num_grids: int,
    spacing: float,
    amount_per_grid: float,
    mode: str,
    leverage: int,
    total_investment_usdt: float,
) -> str:  # noqa: E501
    """Erzeugt einen lesbaren Zusammenfassungstext fuer das Grid."""
    lines = [
        f"Grid-Konfiguration fuer {symbol}",
        f"  Modus      : {mode.upper()}",
        f"  Bereich    : {lower_price} - {upper_price}",
        f"  Grid-Stufen: {num_grids}",
        f"  Abstand    : {spacing:.4f} ({spacing / upper_price * 100:.2f}% vom Oberwert)",
        f"  Menge/Grid : {amount_per_grid:.6f} Coins",
        f"  Kapital    : {total_investment_usdt} USDT",
        f"  Hebel      : {leverage}x",
    ]
    return "\n".join(lines)
