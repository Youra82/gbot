# src/gbot/analysis/fibonacci.py
"""
Fibonacci Retracement Analyse fuer den gbot.

Ablauf:
  1. OHLCV-Daten von Bitget holen (oeffentlicher Endpoint, kein API-Key noetig)
  2. Swing High und Swing Low im Betrachtungszeitraum finden
  3. Fibonacci-Level berechnen (0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%)
  4. Aktuellen Preis mit Fibonacci-Levels vergleichen
  5. Die zwei guenstigsten Level als Grid-Grenzen vorschlagen

Verwendung als Modul:
  from gbot.analysis.fibonacci import auto_fib_analysis

Verwendung als Skript (fuer run_pipeline.sh):
  python3 src/gbot/analysis/fibonacci.py --symbol BTC/USDT:USDT --timeframe 4h --lookback 200
"""

import argparse
import json
import sys
import os
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Standard Fibonacci-Verhaeltnisse
FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_LABELS = ['0.0%', '23.6%', '38.2%', '50.0%', '61.8%', '78.6%', '100%']

# Goldene Zone — staerkste Retracement-Bereich
GOLDEN_ZONE = ('38.2%', '61.8%')


# ---------------------------------------------------------------------------
# Daten holen
# ---------------------------------------------------------------------------

BATCH_SIZE = 1000   # Max Kerzen pro Bitget-API-Request

PROJECT_ROOT_FIB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
SECRET_PATH = os.path.join(PROJECT_ROOT_FIB, 'secret.json')


def _make_exchange():
    """
    Erstellt einen ccxt.bitget Exchange.
    Nutzt API-Key aus secret.json wenn vorhanden (mehr historische Daten),
    faellt sonst auf oeffentlichen Endpoint zurueck.
    """
    import ccxt
    opts = {'enableRateLimit': True, 'options': {'defaultType': 'swap'}}
    if os.path.exists(SECRET_PATH):
        try:
            with open(SECRET_PATH, 'r') as f:
                secrets = json.load(f)
            api = secrets.get('gbot', [{}])[0]
            if api.get('apiKey'):
                opts['apiKey'] = api['apiKey']
                opts['secret'] = api['secret']
                opts['password'] = api.get('password', '')
        except Exception:
            pass
    return ccxt.bitget(opts)


def fetch_ohlcv_public(symbol: str, timeframe: str = '4h', lookback: int = 200) -> pd.DataFrame:
    """
    Holt OHLCV-Daten von Bitget.
    Nutzt API-Key aus secret.json fuer volle historische Tiefe (wie jaegerbot).
    Faellt auf public Endpoint zurueck wenn kein Key vorhanden.
    Unterstuetzt automatische Pagination fuer lookback > 1000 Kerzen.
    """
    import time as _time
    try:
        import ccxt
    except ImportError:
        raise ImportError("ccxt ist nicht installiert. Bitte 'pip install ccxt' ausfuehren.")

    exchange = _make_exchange()

    try:
        tf_ms = exchange.parse_timeframe(timeframe) * 1000
        now_ms = exchange.milliseconds()
        target_since = now_ms - lookback * tf_ms

        if lookback <= BATCH_SIZE:
            raw = exchange.fetch_ohlcv(symbol, timeframe, since=target_since, limit=lookback)
        else:
            # Pagination: vorwaerts ab target_since bis jetzt
            current_since = target_since
            raw = []
            max_batches = (lookback // BATCH_SIZE) + 10
            for _ in range(max_batches):
                batch = exchange.fetch_ohlcv(
                    symbol, timeframe, since=current_since, limit=BATCH_SIZE
                )
                if not batch:
                    break
                raw.extend(batch)
                last_ts = batch[-1][0]
                next_since = last_ts + tf_ms
                if next_since >= now_ms or next_since <= current_since:
                    break
                current_since = next_since
                _time.sleep(0.2)
            # Duplikate entfernen und nur die letzten `lookback` Kerzen
            seen = set()
            deduped = []
            for c in raw:
                if c[0] not in seen:
                    seen.add(c[0])
                    deduped.append(c)
            raw = sorted(deduped, key=lambda x: x[0])[-lookback:]
    except Exception as e:
        raise RuntimeError(f"Fehler beim Abrufen von OHLCV fuer {symbol} ({timeframe}): {e}")

    if not raw:
        raise RuntimeError(f"Keine OHLCV-Daten fuer {symbol} ({timeframe}) erhalten.")

    df = pd.DataFrame(raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep='last')]
    return df


# ---------------------------------------------------------------------------
# Swing High / Low Erkennung
# ---------------------------------------------------------------------------

def find_swing_high_low(
    df: pd.DataFrame,
    swing_window: int = 10,
) -> dict:
    """
    Findet den signifikantesten Swing High und Swing Low im DataFrame.

    Methode:
      - Sucht lokale Extrempunkte: ein Hoch ist ein lokales Hoch wenn es hoeher
        ist als alle `swing_window` Kerzen links und rechts davon.
      - Von allen gefundenen Swing Highs wird der hoechste genommen.
      - Von allen gefundenen Swing Lows wird der niedrigste genommen.
      - Fallback: absolutes Max/Min wenn keine Swing-Punkte gefunden.

    Returns:
        dict mit swing_high, swing_low, high_idx, low_idx, high_time, low_time
    """
    highs = df['high'].values
    lows = df['low'].values
    n = len(highs)

    w = max(2, min(swing_window, n // 5))  # Fenster auf sinnvolle Groesse begrenzen

    swing_high_candidates = []
    swing_low_candidates = []

    for i in range(w, n - w):
        left_h = highs[max(0, i - w):i]
        right_h = highs[i + 1:min(n, i + w + 1)]
        if len(left_h) > 0 and len(right_h) > 0:
            if highs[i] >= max(left_h) and highs[i] >= max(right_h):
                swing_high_candidates.append((i, highs[i]))

        left_l = lows[max(0, i - w):i]
        right_l = lows[i + 1:min(n, i + w + 1)]
        if len(left_l) > 0 and len(right_l) > 0:
            if lows[i] <= min(left_l) and lows[i] <= min(right_l):
                swing_low_candidates.append((i, lows[i]))

    # Hoechsten Swing High / niedrigsten Swing Low nehmen
    if swing_high_candidates:
        high_idx, swing_high = max(swing_high_candidates, key=lambda x: x[1])
    else:
        high_idx = int(np.argmax(highs))
        swing_high = float(highs[high_idx])

    if swing_low_candidates:
        low_idx, swing_low = min(swing_low_candidates, key=lambda x: x[1])
    else:
        low_idx = int(np.argmin(lows))
        swing_low = float(lows[low_idx])

    high_time = str(df.index[high_idx]) if high_idx < len(df) else '?'
    low_time = str(df.index[low_idx]) if low_idx < len(df) else '?'

    return {
        'swing_high': float(swing_high),
        'swing_low': float(swing_low),
        'high_idx': high_idx,
        'low_idx': low_idx,
        'high_time': high_time,
        'low_time': low_time,
        'trend': 'uptrend' if low_idx < high_idx else 'downtrend',
    }


# ---------------------------------------------------------------------------
# Fibonacci-Level berechnen
# ---------------------------------------------------------------------------

def calculate_fib_levels(swing_low: float, swing_high: float) -> dict:
    """
    Berechnet Fibonacci-Retracement-Level zwischen Swing Low und Swing High.

    Fibonacci wird immer von LOW zu HIGH gemessen:
      0%   = Swing Low   (staerkste Unterstuetzung)
      100% = Swing High  (staerkster Widerstand)
      38.2% - 61.8% = Goldene Zone (groesste Wahrscheinlichkeit fuer Bounce)

    Returns:
        dict: {label: price} sortiert von unten nach oben
    """
    if swing_high <= swing_low:
        raise ValueError(f"swing_high ({swing_high}) muss groesser als swing_low ({swing_low}) sein.")

    span = swing_high - swing_low
    levels = {}
    for label, ratio in zip(FIB_LABELS, FIB_RATIOS):
        levels[label] = round(swing_low + ratio * span, 8)

    return levels


def get_sorted_fib_prices(fib_levels: dict) -> list:
    """Gibt Fibonacci-Preise sortiert von niedrig nach hoch zurueck."""
    return sorted(fib_levels.values())


# ---------------------------------------------------------------------------
# Grid-Bereich aus Fibonacci bestimmen
# ---------------------------------------------------------------------------

def find_best_grid_range(
    fib_levels: dict,
    current_price: float,
    prefer_golden_zone: bool = False,
) -> dict:
    """
    Bestimmt die optimalen Grid-Grenzen basierend auf Fibonacci-Levels.

    Strategie:
      - Standard: das naechste Fib-Level unterhalb und oberhalb des aktuellen Preises
      - Golden Zone: erzwingt 38.2% als untere und 61.8% als obere Grenze
        (nur wenn aktueller Preis im Bereich 38.2% - 61.8% liegt)

    Returns:
        dict mit lower_price, upper_price, lower_label, upper_label, in_golden_zone
    """
    prices_sorted = get_sorted_fib_prices(fib_levels)
    label_by_price = {round(v, 8): k for k, v in fib_levels.items()}

    # Preis-Position ermitteln
    below = [p for p in prices_sorted if p <= current_price]
    above = [p for p in prices_sorted if p > current_price]

    if not below:
        below = [prices_sorted[0]]
    if not above:
        above = [prices_sorted[-1]]

    lower = below[-1]   # hoechstes Level unterhalb
    upper = above[0]    # niedrigstes Level oberhalb

    lower_label = label_by_price.get(round(lower, 8), '?')
    upper_label = label_by_price.get(round(upper, 8), '?')

    # Golden Zone pruefen
    golden_low = fib_levels.get('38.2%', 0)
    golden_high = fib_levels.get('61.8%', 0)
    in_golden_zone = golden_low <= current_price <= golden_high

    if prefer_golden_zone and in_golden_zone:
        lower = golden_low
        upper = golden_high
        lower_label = '38.2%'
        upper_label = '61.8%'

    return {
        'lower_price': lower,
        'upper_price': upper,
        'lower_label': lower_label,
        'upper_label': upper_label,
        'in_golden_zone': in_golden_zone,
        'current_price': current_price,
    }


def find_all_level_pairs(fib_levels: dict, current_price: float) -> list:
    """
    Gibt alle moeglichen Fibonacci-Level-Paare zurueck (fuer interaktive Auswahl).

    Returns:
        Liste von dicts: {idx, lower_label, lower_price, upper_label, upper_price, width_pct}
    """
    prices = get_sorted_fib_prices(fib_levels)
    label_by_price = {round(v, 8): k for k, v in fib_levels.items()}

    pairs = []
    for i in range(len(prices) - 1):
        lower = prices[i]
        upper = prices[i + 1]
        label_l = label_by_price.get(round(lower, 8), '?')
        label_u = label_by_price.get(round(upper, 8), '?')
        mid = (lower + upper) / 2
        width_pct = (upper - lower) / mid * 100
        contains_price = lower <= current_price <= upper
        pairs.append({
            'idx': i + 1,
            'lower_label': label_l,
            'lower_price': lower,
            'upper_label': label_u,
            'upper_price': upper,
            'width_pct': round(width_pct, 3),
            'contains_current_price': contains_price,
        })

    return pairs


# ---------------------------------------------------------------------------
# Haupt-Analyse
# ---------------------------------------------------------------------------

def auto_fib_analysis(
    symbol: str,
    timeframe: str = '4h',
    lookback: int = 200,
    swing_window: int = 10,
    prefer_golden_zone: bool = False,
) -> dict:
    """
    Vollstaendige Fibonacci-Analyse fuer ein Symbol.

    Returns:
        dict mit allen Ergebnissen: fib_levels, swing_points, suggested_range, pairs, current_price
    """
    logger.info(f"Lade OHLCV fuer {symbol} ({timeframe}, {lookback} Kerzen)...")
    df = fetch_ohlcv_public(symbol, timeframe, lookback)

    current_price = float(df['close'].iloc[-1])
    logger.info(f"Aktueller Preis: {current_price}")

    swing = find_swing_high_low(df, swing_window=swing_window)
    logger.info(f"Swing High: {swing['swing_high']} @ {swing['high_time']}")
    logger.info(f"Swing Low : {swing['swing_low']} @ {swing['low_time']}")

    fib_levels = calculate_fib_levels(swing['swing_low'], swing['swing_high'])
    suggested = find_best_grid_range(fib_levels, current_price, prefer_golden_zone)
    pairs = find_all_level_pairs(fib_levels, current_price)

    return {
        'symbol': symbol,
        'timeframe': timeframe,
        'lookback': lookback,
        'current_price': current_price,
        'swing_points': swing,
        'fib_levels': fib_levels,
        'suggested_range': suggested,
        'all_pairs': pairs,
    }


# ---------------------------------------------------------------------------
# Ausgabe-Formatierung
# ---------------------------------------------------------------------------

def print_fib_table(analysis: dict):
    """Gibt eine formatierte Tabelle der Fibonacci-Level aus."""
    sym = analysis['symbol']
    tf = analysis['timeframe']
    current = analysis['current_price']
    swing = analysis['swing_points']
    fib = analysis['fib_levels']
    suggested = analysis['suggested_range']
    pairs = analysis['all_pairs']

    w = 60
    print('=' * w)
    print(f"  Fibonacci Retracement — {sym} ({tf})")
    print('=' * w)
    print(f"  Swing High : {swing['swing_high']:>15,.4f}  @ {swing['high_time'][:16]}")
    print(f"  Swing Low  : {swing['swing_low']:>15,.4f}  @ {swing['low_time'][:16]}")
    print(f"  Trend      : {swing['trend'].upper()}")
    print(f"  Range      : {swing['swing_high'] - swing['swing_low']:>15,.4f}")
    print('-' * w)
    print(f"  {'Level':<8}  {'Preis':>15}  {'Abstand zum Preis':>20}  Marker")
    print('-' * w)

    for label, price in sorted(fib.items(), key=lambda x: x[1], reverse=True):
        diff = price - current
        diff_pct = diff / current * 100
        sign = '+' if diff >= 0 else ''
        is_suggested_lower = abs(price - suggested['lower_price']) < 1e-4
        is_suggested_upper = abs(price - suggested['upper_price']) < 1e-4
        is_current_zone = suggested['lower_price'] <= price <= suggested['upper_price']

        marker = ''
        if abs(price - current) < (fib['100%'] - fib['0.0%']) * 0.01:
            marker = '<-- Aktueller Preis'
        elif is_suggested_upper:
            marker = '<-- GRID OBEN'
        elif is_suggested_lower:
            marker = '<-- GRID UNTEN'
        elif label in GOLDEN_ZONE:
            marker = '(Goldene Zone)'

        print(f"  {label:<8}  {price:>15,.4f}  {sign}{diff_pct:>+18.2f}%  {marker}")

    print('=' * w)
    print(f"  Aktueller Preis: {current:,.4f}")
    print(f"  In Goldener Zone (38.2%-61.8%): {'JA' if suggested['in_golden_zone'] else 'NEIN'}")
    print('=' * w)
    print(f"\n  Vorgeschlagener Grid-Bereich:")
    print(f"    Unten ({suggested['lower_label']}): {suggested['lower_price']:,.4f}")
    print(f"    Oben  ({suggested['upper_label']}): {suggested['upper_price']:,.4f}")
    print(f"    Breite: {(suggested['upper_price'] - suggested['lower_price']):.4f}"
          f"  ({(suggested['upper_price'] - suggested['lower_price']) / current * 100:.2f}% vom Preis)")

    print(f"\n  Alle moeglichen Grid-Bereiche:")
    print(f"  {'Nr':<4}  {'Von':<8}  {'Preis Von':>15}  {'Bis':<8}  {'Preis Bis':>15}  {'Breite%':>8}  Aktuell")
    print("  " + "-" * 68)
    for p in pairs:
        current_marker = '<-- Preis hier' if p['contains_current_price'] else ''
        print(
            f"  {p['idx']:<4}  {p['lower_label']:<8}  {p['lower_price']:>15,.4f}"
            f"  {p['upper_label']:<8}  {p['upper_price']:>15,.4f}"
            f"  {p['width_pct']:>8.3f}%  {current_marker}"
        )
    print('=' * w)


# ---------------------------------------------------------------------------
# CLI-Modus (fuer run_pipeline.sh)
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="gbot Fibonacci Retracement Analyse")
    parser.add_argument('--symbol', required=True, help="Symbol z.B. BTC/USDT:USDT")
    parser.add_argument('--timeframe', default='4h', help="Zeitrahmen z.B. 4h")
    parser.add_argument('--lookback', type=int, default=200, help="Anzahl Kerzen")
    parser.add_argument('--swing_window', type=int, default=10, help="Swing-Erkennungsfenster")
    parser.add_argument('--golden_zone', action='store_true', help="Goldene Zone bevorzugen")
    parser.add_argument('--json', action='store_true', help="Ausgabe als JSON (fuer Shell-Scripting)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    try:
        analysis = auto_fib_analysis(
            symbol=args.symbol,
            timeframe=args.timeframe,
            lookback=args.lookback,
            swing_window=args.swing_window,
            prefer_golden_zone=args.golden_zone,
        )
    except Exception as e:
        if args.json:
            print(json.dumps({'error': str(e)}))
        else:
            print(f"Fehler: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        # Fuer Shell-Scripting: kompaktes JSON
        out = {
            'current_price': analysis['current_price'],
            'swing_high': analysis['swing_points']['swing_high'],
            'swing_low': analysis['swing_points']['swing_low'],
            'trend': analysis['swing_points']['trend'],
            'fib_levels': analysis['fib_levels'],
            'suggested_lower': analysis['suggested_range']['lower_price'],
            'suggested_upper': analysis['suggested_range']['upper_price'],
            'suggested_lower_label': analysis['suggested_range']['lower_label'],
            'suggested_upper_label': analysis['suggested_range']['upper_label'],
            'in_golden_zone': analysis['suggested_range']['in_golden_zone'],
            'all_pairs': analysis['all_pairs'],
        }
        print(json.dumps(out, indent=2))
    else:
        print_fib_table(analysis)


if __name__ == '__main__':
    main()
