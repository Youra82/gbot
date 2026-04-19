# src/gbot/utils/trade_manager.py
"""
Grid Trading Manager - Kern-Logik des gbot.

Ablauf pro Zyklus:
  1. Grid initialisieren (falls noch nicht geschehen):
     - Hebel & Margin setzen
     - Grid-Bereich berechnen (Fibonacci automatisch ODER manuell aus Config)
     - Initiale Orders platzieren

  2. Laufender Zyklus:
     a) Fibonacci-Rebalancing pruefen (wenn fibonacci.enabled = true):
        - Aktuellen Preis holen
        - Liegt Preis ausserhalb des Grid-Bereichs?
        - Cooldown abgelaufen?
        → Wenn ja: Alle Orders stornieren, neuen Fib-Bereich berechnen, neu aufbauen
     b) Fills erkennen und Nachfolge-Orders platzieren

Tracker-Datei: artifacts/tracker/<symbol_clean>_grid.json

Fibonacci-Config (in der Strategie-Config unter "grid.fibonacci"):
  enabled                     : true/false
  timeframe                   : z.B. "4h"
  lookback                    : Anzahl Kerzen, z.B. 200
  swing_window                : Swing-Erkennungsfenster, z.B. 10
  prefer_golden_zone          : true = bevorzuge 38.2%-61.8%
  rebalance_on_break          : true = auto-rebalance wenn Preis Grid verlaesst
  min_rebalance_interval_hours: Mindestzeit zwischen zwei Rebalancings (z.B. 4)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from gbot.utils.telegram import send_message
from gbot.utils.exchange import Exchange
from gbot.strategy.grid_logic import (
    calculate_grid_levels,
    get_grid_spacing,
    calculate_amount_per_grid,
    split_levels_by_price,
    format_grid_summary,
    find_next_sell_level,
    find_next_buy_level,
    price_in_range,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
TRACKER_DIR = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker')
os.makedirs(TRACKER_DIR, exist_ok=True)

MIN_NOTIONAL_USDT = 5.0


# ---------------------------------------------------------------------------
# Tracker-Hilfsfunktionen
# ---------------------------------------------------------------------------

def get_tracker_file_path(symbol: str) -> str:
    safe = symbol.replace('/', '').replace(':', '').replace('-', '')
    return os.path.join(TRACKER_DIR, f"{safe}_grid.json")


def read_tracker(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Tracker konnte nicht gelesen werden ({path}): {e}")
    return {}


def write_tracker(path: str, data: dict):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    except OSError as e:
        logger.error(f"Tracker konnte nicht geschrieben werden ({path}): {e}")


# ---------------------------------------------------------------------------
# Fibonacci-Bereich berechnen
# ---------------------------------------------------------------------------

def _get_fib_range(params: dict) -> Tuple[float, float, dict]:
    """
    Berechnet den Grid-Bereich automatisch via Fibonacci Retracement.

    Laedt aktuelle OHLCV-Daten (kein API-Key noetig), erkennt Swing High/Low
    und waehlt die guenstigsten Fibonacci-Level als Grid-Grenzen.

    Returns:
        (lower_price, upper_price, analysis_dict)
    """
    from gbot.analysis.fibonacci import auto_fib_analysis

    fib_cfg = params['grid']['fibonacci']
    symbol = params['market']['symbol']

    analysis = auto_fib_analysis(
        symbol=symbol,
        timeframe=fib_cfg.get('timeframe', '4h'),
        lookback=fib_cfg.get('lookback', 200),
        swing_window=fib_cfg.get('swing_window', 10),
        prefer_golden_zone=fib_cfg.get('prefer_golden_zone', False),
    )

    suggested = analysis['suggested_range']
    lower = suggested['lower_price']
    upper = suggested['upper_price']
    lower_label = suggested['lower_label']
    upper_label = suggested['upper_label']

    logger.info(
        f"Fibonacci-Range: {lower:.4f} ({lower_label}) - {upper:.4f} ({upper_label}) | "
        f"Swing High: {analysis['swing_points']['swing_high']:.4f} | "
        f"Swing Low: {analysis['swing_points']['swing_low']:.4f} | "
        f"Trend: {analysis['swing_points']['trend'].upper()}"
    )

    return lower, upper, analysis


def _resolve_grid_range(params: dict, log: logging.Logger) -> Tuple[float, float, Optional[dict]]:
    """
    Bestimmt lower_price und upper_price — entweder via Fibonacci oder aus der Config.

    Returns:
        (lower_price, upper_price, fib_analysis_or_None)
    """
    fib_cfg = params['grid'].get('fibonacci', {})

    if fib_cfg.get('enabled', False):
        log.info("Fibonacci-Modus: Berechne Grid-Bereich automatisch...")
        try:
            lower, upper, analysis = _get_fib_range(params)
            return lower, upper, analysis
        except Exception as e:
            log.error(f"Fibonacci-Analyse fehlgeschlagen, Fallback auf Config-Werte: {e}")

    # Fallback: manuelle Werte aus Config
    lower = params['grid'].get('lower_price')
    upper = params['grid'].get('upper_price')
    if lower is None or upper is None:
        raise ValueError(
            "Weder Fibonacci noch manuelle lower_price/upper_price in der Config definiert."
        )
    return float(lower), float(upper), None


# ---------------------------------------------------------------------------
# Orders platzieren (Hilfsfunktion)
# ---------------------------------------------------------------------------

def _place_grid_orders(
    exchange: Exchange,
    symbol: str,
    levels: list,
    current_price: float,
    amount_per_grid: float,
    mode: str,
    log: logging.Logger,
) -> dict:
    """
    Platziert alle initialen Grid-Orders (Buy und Sell).
    Wird sowohl bei Erstinitialisierung als auch beim Rebalancing verwendet.

    Returns:
        dict: active_orders {price_str: order_info}
    """
    buy_levels, sell_levels = split_levels_by_price(levels, current_price, mode)
    log.info(f"  Kauf-Levels  ({len(buy_levels)}): {[round(p, 4) for p in buy_levels]}")
    log.info(f"  Verkauf-Levels ({len(sell_levels)}): {[round(p, 4) for p in sell_levels]}")

    active_orders = {}
    placed = 0
    now = datetime.now(timezone.utc).isoformat()

    for side, price_list in [('buy', buy_levels), ('sell', sell_levels)]:
        for price in price_list:
            price_r = exchange.round_price(symbol, price)
            try:
                order = exchange.place_limit_order(symbol, side, amount_per_grid, price_r)
                active_orders[str(price_r)] = {
                    'order_id': order['id'],
                    'side': side,
                    'price': price_r,
                    'amount': amount_per_grid,
                    'placed_at': now,
                }
                placed += 1
                time.sleep(0.3)
            except Exception as e:
                log.error(f"  {side.upper()}-Order bei {price_r} fehlgeschlagen: {e}")

    log.info(f"  {placed} Orders platziert.")
    return active_orders


# ---------------------------------------------------------------------------
# Grid initialisieren
# ---------------------------------------------------------------------------

def initialize_grid(exchange: Exchange, params: dict, log: logging.Logger) -> dict:
    """
    Erstinitialisierung des Grids.
    Berechnet den Grid-Bereich (Fibonacci oder manuell), setzt Hebel und Margin,
    platziert alle initialen Orders und legt den Tracker an.
    """
    symbol = params['market']['symbol']
    grid_cfg = params['grid']
    risk_cfg = params['risk']

    num_grids = grid_cfg['num_grids']
    mode = grid_cfg.get('grid_mode', 'neutral').lower()
    leverage = risk_cfg.get('leverage', 1)
    margin_mode = risk_cfg.get('margin_mode', 'isolated')
    total_investment = risk_cfg['total_investment_usdt']

    log.info(f"Initialisiere Grid fuer {symbol} | Stufen: {num_grids} | Modus: {mode} | Hebel: {leverage}x")

    # 1. Hebel & Margin setzen
    exchange.set_margin_mode(symbol, margin_mode)
    exchange.set_leverage(symbol, leverage, margin_mode)
    time.sleep(1)

    # 2. Aktuellen Preis
    current_price = exchange.get_current_price(symbol)
    log.info(f"  Aktueller Preis: {current_price:.4f}")

    # 3. Grid-Bereich bestimmen (Fibonacci oder manuell)
    lower, upper, fib_analysis = _resolve_grid_range(params, log)

    if not price_in_range(current_price, lower, upper):
        log.warning(
            f"  Aktueller Preis {current_price:.4f} liegt AUSSERHALB des berechneten "
            f"Bereichs [{lower:.4f}, {upper:.4f}]. Initialisierung laeuft trotzdem."
        )

    # 4. Grid-Levels und Menge berechnen
    levels = calculate_grid_levels(lower, upper, num_grids)
    spacing = get_grid_spacing(lower, upper, num_grids)
    amount_per_grid = calculate_amount_per_grid(total_investment, num_grids, current_price, leverage)
    amount_per_grid = exchange.round_amount(symbol, amount_per_grid)

    log.info(format_grid_summary(symbol, lower, upper, num_grids, spacing, amount_per_grid, mode, leverage, total_investment))

    # Min-Notional-Check
    notional = amount_per_grid * current_price / leverage
    if notional < MIN_NOTIONAL_USDT:
        log.warning(
            f"  Notional pro Grid ({notional:.2f} USDT) < Minimum ({MIN_NOTIONAL_USDT} USDT). "
            f"Erhoehe total_investment oder reduziere num_grids."
        )

    # 5. Orders platzieren
    active_orders = _place_grid_orders(exchange, symbol, levels, current_price, amount_per_grid, mode, log)

    # 6. Fib-Meta fuer Tracker speichern
    fib_meta = {}
    if fib_analysis:
        fib_meta = {
            'lower_label': fib_analysis['suggested_range']['lower_label'],
            'upper_label': fib_analysis['suggested_range']['upper_label'],
            'swing_high': fib_analysis['swing_points']['swing_high'],
            'swing_low': fib_analysis['swing_points']['swing_low'],
            'trend': fib_analysis['swing_points']['trend'],
            'timeframe': fib_analysis['timeframe'],
            'calculated_at': datetime.now(timezone.utc).isoformat(),
        }

    return {
        'symbol': symbol,
        'initialized': True,
        'initialized_at': datetime.now(timezone.utc).isoformat(),
        'rebalance_count': 0,
        'last_rebalance_at': None,
        'fib_meta': fib_meta,
        'grid_config': {
            'lower_price': lower,
            'upper_price': upper,
            'num_grids': num_grids,
            'spacing': spacing,
            'mode': mode,
            'levels': levels,
            'amount_per_grid': amount_per_grid,
            'leverage': leverage,
            'margin_mode': margin_mode,
            'total_investment_usdt': total_investment,
        },
        'active_orders': active_orders,
        'performance': {
            'total_fills': 0,
            'buy_fills': 0,
            'sell_fills': 0,
            'realized_pnl_usdt': 0.0,
            'fee_paid_usdt': 0.0,
            'last_fill_at': None,
        },
    }


# ---------------------------------------------------------------------------
# Dynamisches Fibonacci-Rebalancing
# ---------------------------------------------------------------------------

def maybe_rebalance(
    exchange: Exchange,
    params: dict,
    tracker: dict,
    telegram_config: dict,
    log: logging.Logger,
) -> dict:
    """
    Prueft ob ein Fibonacci-Rebalancing noetig ist und fuehrt es durch.

    Trigger: Preis liegt ausserhalb des aktuellen Grid-Bereichs.
    Cooldown: min_rebalance_interval_hours aus der Config.

    Ablauf beim Rebalancing:
      1. Alle offenen Orders stornieren
      2. Neue Fibonacci-Analyse mit aktuellen OHLCV-Daten
      3. Neuen Grid-Bereich berechnen und Orders platzieren
      4. Telegram-Benachrichtigung
    """
    fib_cfg = params['grid'].get('fibonacci', {})
    fib_enabled = fib_cfg.get('enabled', False) and fib_cfg.get('rebalance_on_break', True)

    symbol = tracker.get('symbol', params['market']['symbol'])
    gc = tracker.get('grid_config', {})
    lower = gc.get('lower_price', 0)
    upper = gc.get('upper_price', float('inf'))

    # Aktuellen Preis pruefen
    try:
        current_price = exchange.get_current_price(symbol)
    except Exception as e:
        log.error(f"Rebalancing-Check: Preis nicht abrufbar: {e}")
        return tracker

    if price_in_range(current_price, lower, upper):
        return tracker  # Preis noch im Bereich → nichts zu tun

    direction = 'OBEN' if current_price > upper else 'UNTEN'
    distance_pct = abs(current_price - (upper if current_price > upper else lower)) / current_price * 100
    log.warning(
        f"Preis {current_price:.4f} hat Grid nach {direction} verlassen "
        f"(Bereich: {lower:.4f}-{upper:.4f}, Abstand: {distance_pct:.2f}%)"
    )

    # Grid-SL: immer sofort ausfuehren (kein Cooldown), unabhaengig von Fibonacci
    log.info(f"Grid-SL: Orders stornieren und Positionen schliessen ({direction})...")
    try:
        exchange.cancel_all_orders(symbol)
        time.sleep(1)
        exchange.close_all_positions(symbol)
        time.sleep(1)
    except Exception as e:
        log.error(f"Grid-SL Positions-Closing fehlgeschlagen: {e}")

    # Ohne Fibonacci: Grid bleibt gestoppt (kein Neuaufbau), Tracker zuruecksetzen
    if not fib_enabled:
        tracker['initialized'] = False
        send_message(
            telegram_config.get('bot_token'), telegram_config.get('chat_id'),
            f"GRID-SL: {symbol}\nPreis hat Grid nach {direction} verlassen.\n"
            f"Positionen geschlossen. Grid wird beim naechsten Lauf neu initialisiert."
        )
        return tracker

    # --- Fibonacci-Rebalancing ---
    log.info("Starte Fibonacci-Rebalancing...")

    try:
        # Neue Fibonacci-Range berechnen
        new_lower, new_upper, fib_analysis = _get_fib_range(params)
        log.info(f"  Neuer Grid-Bereich: {new_lower:.4f} - {new_upper:.4f}")

        # 3. Grid neu berechnen
        num_grids = gc.get('num_grids', params['grid']['num_grids'])
        leverage = gc.get('leverage', params['risk']['leverage'])
        total_investment = gc.get('total_investment_usdt', params['risk']['total_investment_usdt'])
        mode = gc.get('mode', params['grid'].get('grid_mode', 'neutral'))

        new_levels = calculate_grid_levels(new_lower, new_upper, num_grids)
        new_spacing = get_grid_spacing(new_lower, new_upper, num_grids)
        new_amount = calculate_amount_per_grid(total_investment, num_grids, current_price, leverage)
        new_amount = exchange.round_amount(symbol, new_amount)

        # 4. Neue Orders platzieren
        new_orders = _place_grid_orders(
            exchange, symbol, new_levels, current_price, new_amount, mode, log
        )

        # 5. Tracker aktualisieren
        now = datetime.now(timezone.utc).isoformat()
        tracker['grid_config'].update({
            'lower_price': new_lower,
            'upper_price': new_upper,
            'levels': new_levels,
            'spacing': new_spacing,
            'amount_per_grid': new_amount,
        })
        tracker['active_orders'] = new_orders
        tracker['last_rebalance_at'] = now
        tracker['rebalance_count'] = tracker.get('rebalance_count', 0) + 1
        tracker['fib_meta'] = {
            'lower_label': fib_analysis['suggested_range']['lower_label'],
            'upper_label': fib_analysis['suggested_range']['upper_label'],
            'swing_high': fib_analysis['swing_points']['swing_high'],
            'swing_low': fib_analysis['swing_points']['swing_low'],
            'trend': fib_analysis['swing_points']['trend'],
            'timeframe': fib_analysis['timeframe'],
            'calculated_at': now,
        }

        log.info(
            f"Rebalancing #{tracker['rebalance_count']} abgeschlossen: "
            f"{len(new_orders)} neue Orders | Bereich: {new_lower:.4f} - {new_upper:.4f}"
        )

        # 6. Telegram-Benachrichtigung
        try:
            swing = fib_analysis['swing_points']
            suggested = fib_analysis['suggested_range']
            header = f"Grid-SL + Rebalancing #{tracker['rebalance_count']}: {symbol}"
            msg = (
                f"{header}\n\n"
                f"Preis hat Grid nach {direction} verlassen.\n"
                f"Positionen geschlossen. Neuer Fibonacci-Bereich:\n"
                f"  Unten: {new_lower:.4f} ({suggested['lower_label']})\n"
                f"  Oben : {new_upper:.4f} ({suggested['upper_label']})\n\n"
                f"Swing High: {swing['swing_high']:.4f}\n"
                f"Swing Low : {swing['swing_low']:.4f}\n"
                f"Trend: {swing['trend'].upper()}\n\n"
                f"{len(new_orders)} Orders aktiv."
            )
            send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), msg)
        except Exception as e:
            log.error(f"Telegram-Rebalancing-Benachrichtigung fehlgeschlagen: {e}")

    except Exception as e:
        log.error(f"Rebalancing fehlgeschlagen: {e}", exc_info=True)
        # Tracker unveraendert zurueckgeben — Bot laeuft mit altem Grid weiter

    return tracker


# ---------------------------------------------------------------------------
# Laufender Zyklus
# ---------------------------------------------------------------------------

def run_grid_cycle(
    exchange: Exchange,
    params: dict,
    tracker: dict,
    telegram_config: dict,
    log: logging.Logger,
) -> dict:
    """
    Ein normaler Zyklus: Fills erkennen und Nachfolge-Orders platzieren.
    Das Rebalancing wird bereits VOR diesem Aufruf in full_grid_cycle geprueft.
    """
    symbol = params['market']['symbol']
    gc = tracker['grid_config']
    levels = gc['levels']
    spacing = gc['spacing']
    amount_per_grid = gc['amount_per_grid']
    mode = gc['mode']

    # Offene Orders von der Boerse holen
    try:
        open_orders = exchange.fetch_open_orders(symbol)
    except Exception as e:
        log.error(f"Fehler beim Abrufen offener Orders: {e}")
        return tracker

    open_order_ids = {o['id'] for o in open_orders}
    active_orders = tracker.get('active_orders', {})
    perf = tracker.setdefault('performance', {
        'total_fills': 0, 'buy_fills': 0, 'sell_fills': 0,
        'realized_pnl_usdt': 0.0, 'fee_paid_usdt': 0.0, 'last_fill_at': None,
    })

    # Fills erkennen: im Tracker vorhandene Orders die nicht mehr offen sind
    filled_entries = []
    repaired_orders = []
    for price_key, order_info in list(active_orders.items()):
        order_id = order_info.get('order_id')
        if order_id not in open_order_ids:
            try:
                fetched = exchange.fetch_order(order_id, symbol)
                status = fetched.get('status', 'unknown')
                if status == 'closed':
                    filled_entries.append((price_key, order_info, fetched))
                else:
                    log.warning(f"Order {order_id} @ {price_key}: Status '{status}' — wird neu platziert.")
                    del active_orders[price_key]
                    try:
                        new_order = exchange.place_limit_order(
                            symbol, order_info['side'], order_info['amount'], order_info['price']
                        )
                        active_orders[price_key] = {**order_info, 'order_id': new_order['id']}
                        repaired_orders.append(f"{order_info['side'].upper()} @ {order_info['price']}")
                        log.info(f"  Order neu platziert: {order_info['side'].upper()} @ {order_info['price']}")
                    except Exception as re:
                        log.error(f"  Neu-Platzierung fehlgeschlagen @ {price_key}: {re}")
            except Exception as e:
                log.error(f"Order {order_id} konnte nicht abgerufen werden — aus Tracker entfernt: {e}")
                del active_orders[price_key]

    # Fills verarbeiten
    for price_key, order_info, fetched_order in filled_entries:
        side = order_info['side']
        fill_price = float(fetched_order.get('average') or fetched_order.get('price') or order_info['price'])
        fill_amount = float(fetched_order.get('filled') or order_info['amount'])
        fill_time = datetime.now(timezone.utc).isoformat()

        log.info(f"Fill: {side.upper()} {fill_amount} {symbol} @ {fill_price:.4f}")

        fee = fill_price * fill_amount * 0.0006  # ~0.06% Taker-Gebuehr
        if side == 'sell':
            pnl = spacing * fill_amount - 2 * fee
            perf['sell_fills'] += 1
        else:
            pnl = -fee
            perf['buy_fills'] += 1

        perf['total_fills'] += 1
        perf['realized_pnl_usdt'] = round(perf.get('realized_pnl_usdt', 0.0) + pnl, 4)
        perf['fee_paid_usdt'] = round(perf.get('fee_paid_usdt', 0.0) + fee, 4)
        perf['last_fill_at'] = fill_time

        del active_orders[price_key]

        # Nachfolge-Order platzieren
        if side == 'buy':
            next_price = find_next_sell_level(fill_price, levels)
            next_side = 'sell'
            next_amount = fill_amount
        else:
            next_price = find_next_buy_level(fill_price, levels)
            next_side = 'buy'
            next_amount = amount_per_grid

        if next_price is not None:
            price_r = exchange.round_price(symbol, next_price)
            key = str(price_r)
            if key not in active_orders:
                try:
                    new_order = exchange.place_limit_order(symbol, next_side, next_amount, price_r)
                    entry = {
                        'order_id': new_order['id'],
                        'side': next_side,
                        'price': price_r,
                        'amount': next_amount,
                        'placed_at': fill_time,
                    }
                    if next_side == 'sell':
                        entry['paired_buy_price'] = fill_price
                    active_orders[key] = entry
                    log.info(f"  -> {next_side.upper()}-Order @ {price_r:.4f}")
                except Exception as e:
                    log.error(f"  {next_side.upper()}-Order @ {price_r} fehlgeschlagen: {e}")
        else:
            edge = 'oberem' if side == 'buy' else 'unterem'
            log.info(f"  Kein naechstes Level (am {edge} Grid-Rand).")

        _send_fill_notification(telegram_config, symbol, side, fill_price, fill_amount, pnl, perf)

    # Grid-Level-Abgleich: fehlende Orders neu platzieren
    current_price = None
    tracked_prices = {float(k) for k in active_orders}
    for level in levels:
        level_r = exchange.round_price(symbol, level)
        if level_r not in tracked_prices:
            if current_price is None:
                try:
                    current_price = exchange.get_current_price(symbol)
                except Exception:
                    break
            side = 'buy' if level_r < current_price else 'sell'
            try:
                new_order = exchange.place_limit_order(symbol, side, amount_per_grid, level_r)
                active_orders[str(level_r)] = {
                    'order_id': new_order['id'],
                    'side': side,
                    'price': level_r,
                    'amount': amount_per_grid,
                    'placed_at': datetime.now(timezone.utc).isoformat(),
                }
                log.info(f"Fehlendes Grid-Level wiederhergestellt: {side.upper()} @ {level_r}")
                time.sleep(0.3)
            except Exception as e:
                log.error(f"Grid-Level {level_r} konnte nicht wiederhergestellt werden: {e}")

    tracker['active_orders'] = active_orders

    if repaired_orders:
        try:
            fib_meta = tracker.get('fib_meta', {})
            fib_info = (
                f"\nFibonacci: {fib_meta.get('lower_label','?')} - {fib_meta.get('upper_label','?')}"
                f" | Trend: {fib_meta.get('trend','?').upper()}"
                f"\nSwing High: {fib_meta.get('swing_high','?')} | Swing Low: {fib_meta.get('swing_low','?')}"
                f" ({fib_meta.get('timeframe','?')})"
            ) if fib_meta else ""
            send_message(
                telegram_config.get('bot_token'),
                telegram_config.get('chat_id'),
                f"\U0001f527 Grid repariert: {symbol}\n\n"
                f"Bereich: {gc['lower_price']:.4f} - {gc['upper_price']:.4f}\n"
                f"Stufen: {gc['num_grids']} | Modus: {gc['mode'].upper()}\n"
                f"Kapital: {gc['total_investment_usdt']} USDT | Hebel: {gc['leverage']}x"
                f"{fib_info}\n\n"
                f"Aktive Orders: {len(active_orders)}",
            )
        except Exception:
            pass

    log.info(
        f"Zyklus | Orders: {len(active_orders)} | "
        f"Fills: {perf['total_fills']} | PnL: {perf['realized_pnl_usdt']:+.4f} USDT"
    )
    return tracker


# ---------------------------------------------------------------------------
# Aufraeum- und Ueberwachungsroutinen
# ---------------------------------------------------------------------------

def check_orphan_positions(
    exchange: Exchange,
    symbol: str,
    tracker: dict,
    telegram_config: dict,
    log: logging.Logger,
):
    """
    Prueft ob offene Positionen existieren, obwohl das Grid nicht initialisiert ist.
    Schliesst verwaiste Positionen automatisch.
    """
    if tracker.get('initialized'):
        return
    positions = exchange.fetch_open_positions(symbol)
    if not positions:
        return
    log.warning(f"Verwaiste Positionen ({len(positions)}) gefunden — Grid nicht aktiv. Schliesse...")
    exchange.close_all_positions(symbol)
    try:
        send_message(
            telegram_config.get('bot_token'),
            telegram_config.get('chat_id'),
            f"\u26a0\ufe0f gbot: Verwaiste Position(en) fuer {symbol} geschlossen.\n"
            f"Grid war nicht aktiv \u2014 automatisch bereinigt.",
        )
    except Exception:
        pass


def auto_clear_cache(log: logging.Logger):
    """
    Loescht Cache-Dateien aelter als auto_clear_cache_days Tage (aus settings.json).
    """
    import glob as _glob
    settings_file = os.path.join(PROJECT_ROOT, 'settings.json')
    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)
        days = int(settings.get('optimization_settings', {}).get('auto_clear_cache_days', 30))
    except Exception:
        days = 30

    cache_dir = os.path.join(PROJECT_ROOT, 'data', 'cache')
    if not os.path.isdir(cache_dir):
        return

    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    deleted = 0
    for path in _glob.glob(os.path.join(cache_dir, '**', '*'), recursive=True):
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                deleted += 1
        except OSError:
            pass
    if deleted:
        log.info(f"Cache-Cleanup: {deleted} Datei(en) aelter als {days} Tage geloescht.")


# ---------------------------------------------------------------------------
# Haupt-Einstiegspunkt
# ---------------------------------------------------------------------------

def full_grid_cycle(exchange: Exchange, params: dict, telegram_config: dict, log: logging.Logger):
    """
    Wird von run.py aufgerufen.
    1. Initialisierung beim ersten Start
    2. Fibonacci-Rebalancing pruefen (falls aktiviert)
    3. Normaler Zyklus (Fills + Nachfolge-Orders)
    """
    symbol = params['market']['symbol']
    tracker_path = get_tracker_file_path(symbol)
    tracker = read_tracker(tracker_path)

    # Cache-Cleanup (loescht nur wenn Dateien aelter als auto_clear_cache_days)
    auto_clear_cache(log)

    # Grid-Reset wenn initialisiert aber keine aktiven Orders mehr vorhanden
    if tracker.get('initialized') and not tracker.get('active_orders'):
        log.warning("Grid initialisiert aber keine aktiven Orders — setze zurueck fuer Neuinitialisierung.")
        tracker['initialized'] = False
        write_tracker(tracker_path, tracker)

    if not tracker.get('initialized'):
        # Verwaiste Positionen schliessen bevor Grid initialisiert wird
        check_orphan_positions(exchange, symbol, tracker, telegram_config, log)
        log.info("Grid noch nicht initialisiert. Starte Erstinitialisierung...")
        try:
            tracker = initialize_grid(exchange, params, log)
            write_tracker(tracker_path, tracker)

            gc = tracker['grid_config']
            fib_meta = tracker.get('fib_meta', {})
            fib_info = (
                f"\nFibonacci: {fib_meta.get('lower_label','?')} - {fib_meta.get('upper_label','?')}"
                f" | Trend: {fib_meta.get('trend','?').upper()}"
                f"\nSwing High: {fib_meta.get('swing_high','?')} | Swing Low: {fib_meta.get('swing_low','?')}"
                f" ({fib_meta.get('timeframe','?')})"
            ) if fib_meta else ""

            msg = (
                f"Grid gestartet: {symbol}\n\n"
                f"Bereich: {gc['lower_price']:.4f} - {gc['upper_price']:.4f}\n"
                f"Stufen: {gc['num_grids']} | Modus: {gc['mode'].upper()}\n"
                f"Kapital: {gc['total_investment_usdt']} USDT | Hebel: {gc['leverage']}x"
                f"{fib_info}\n\n"
                f"Aktive Orders: {len(tracker['active_orders'])}"
            )
            send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), msg)
        except Exception as e:
            log.critical(f"Grid-Initialisierung fehlgeschlagen: {e}", exc_info=True)
            raise
        return

    # Fibonacci-Rebalancing pruefen (bevor normaler Zyklus laeuft)
    tracker = maybe_rebalance(exchange, params, tracker, telegram_config, log)
    write_tracker(tracker_path, tracker)

    # Normaler Zyklus
    tracker = run_grid_cycle(exchange, params, tracker, telegram_config, log)
    write_tracker(tracker_path, tracker)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _send_fill_notification(
    telegram_config: dict,
    symbol: str,
    side: str,
    price: float,
    amount: float,
    pnl: float,
    perf: dict,
):
    try:
        label = 'BUY' if side == 'buy' else 'SELL'
        msg = (
            f"[{label}] Grid-Fill: {symbol}\n"
            f"Preis : {price:.4f}\n"
            f"Menge : {amount}\n"
            f"PnL   : {pnl:+.4f} USDT\n"
            f"Gesamt: {perf['realized_pnl_usdt']:+.4f} USDT | Fills: {perf['total_fills']}"
        )
        send_message(telegram_config.get('bot_token'), telegram_config.get('chat_id'), msg)
    except Exception as e:
        logger.warning(f"Fill-Benachrichtigung fehlgeschlagen: {e}")
