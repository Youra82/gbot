# src/gbot/analysis/show_results.py
"""
Analyse- und Anzeige-Skript fuer den gbot.

Modi:
  1) Grid Status Uebersicht    — alle Tracker-Dateien kompakt
  2) Order-Analyse             — Aufschluesselung nach Grid-Levels
  3) Performance & PnL         — Statistiken und Kennzahlen
  4) Fibonacci-Analyse         — aktueller Range fuer alle aktiven Symbole
"""
import argparse
import glob
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

TRACKER_DIR = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker')
CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'gbot', 'strategy', 'configs')


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def load_trackers() -> list:
    files = sorted(glob.glob(os.path.join(TRACKER_DIR, '*_grid.json')))
    trackers = []
    for path in files:
        try:
            with open(path) as f:
                data = json.load(f)
            trackers.append(data)
        except Exception as e:
            print(f"  [WARNUNG] Tracker konnte nicht gelesen werden ({path}): {e}")
    return trackers


def load_configs() -> list:
    files = sorted(glob.glob(os.path.join(CONFIGS_DIR, 'config_*.json')))
    configs = []
    for path in files:
        try:
            with open(path) as f:
                configs.append((os.path.basename(path), json.load(f)))
        except Exception as e:
            print(f"  [WARNUNG] Config konnte nicht gelesen werden ({path}): {e}")
    return configs


def sep(char='=', width=60):
    print(char * width)


def header(title: str):
    sep()
    print(f"  {title}")
    sep()


# ---------------------------------------------------------------------------
# Modus 1: Grid Status Uebersicht
# ---------------------------------------------------------------------------

def mode_1_status():
    header("Modus 1 — Grid Status Uebersicht")

    trackers = load_trackers()
    configs = load_configs()

    if not trackers and not configs:
        print("\n  Keine Tracker- oder Config-Dateien gefunden.")
        print("  Starte zuerst ./run_pipeline.sh um ein Grid zu konfigurieren.")
        return

    # Configs anzeigen (was konfiguriert ist)
    print(f"\n  Konfigurierte Grids ({len(configs)}):")
    print(f"  {'Symbol':<25} {'Modus':<10} {'Bereich':<25} {'Stufen':>6} {'Kapital':>10} {'Hebel':>6}")
    print("  " + "-" * 82)
    for filename, cfg in configs:
        sym = cfg.get('market', {}).get('symbol', '?')
        g = cfg.get('grid', {})
        r = cfg.get('risk', {})
        mode_str = g.get('grid_mode', '?').upper()
        bereich = f"{g.get('lower_price','?')} - {g.get('upper_price','?')}"
        stufen = g.get('num_grids', '?')
        kapital = f"{r.get('total_investment_usdt','?')} USDT"
        hebel = f"{r.get('leverage','?')}x"
        print(f"  {sym:<25} {mode_str:<10} {bereich:<25} {stufen:>6} {kapital:>10} {hebel:>6}")

    # Aktive Tracker anzeigen
    print(f"\n  Aktive Tracker ({len(trackers)}):")
    for t in trackers:
        symbol = t.get('symbol', 'Unbekannt')
        init = t.get('initialized', False)
        gc = t.get('grid_config', {})
        perf = t.get('performance', {})
        orders = t.get('active_orders', {})

        print(f"\n  Symbol  : {symbol}")
        print(f"  Status  : {'AKTIV' if init else 'NICHT INITIALISIERT'}")
        if gc:
            spacing_pct = gc.get('spacing', 0) / ((gc.get('lower_price', 1) + gc.get('upper_price', 1)) / 2) * 100
            print(f"  Grid    : {gc.get('lower_price')} – {gc.get('upper_price')} | {gc.get('num_grids')} Stufen | {gc.get('mode','?').upper()}")
            print(f"  Spacing : {gc.get('spacing', 0):.4f} ({spacing_pct:.3f}%)")
        pnl = perf.get('realized_pnl_usdt', 0.0)
        fills = perf.get('total_fills', 0)
        print(f"  Orders  : {len(orders)} offen  |  Fills: {fills}  |  PnL: {'+' if pnl >= 0 else ''}{pnl:.4f} USDT")
    sep('-')


# ---------------------------------------------------------------------------
# Modus 2: Order-Analyse nach Grid-Level
# ---------------------------------------------------------------------------

def mode_2_orders():
    header("Modus 2 — Order-Analyse nach Grid-Levels")

    trackers = load_trackers()
    if not trackers:
        print("\n  Keine Tracker-Dateien gefunden.")
        return

    for t in trackers:
        symbol = t.get('symbol', 'Unbekannt')
        orders = t.get('active_orders', {})
        gc = t.get('grid_config', {})
        levels = gc.get('levels', [])

        print(f"\n  Symbol: {symbol}")
        print(f"  {'Preis':>15}  {'Side':<6}  {'Order-ID':<30}  {'Platziert'}")
        print("  " + "-" * 75)

        if not orders:
            print("  (keine offenen Orders im Tracker)")
        else:
            # Sortiert nach Preis (absteigend = höchste zuerst)
            for price_key in sorted(orders.keys(), key=float, reverse=True):
                o = orders[price_key]
                side = o.get('side', '?').upper()
                oid = o.get('order_id', '—')[:28]
                placed = o.get('placed_at', '—')[:19]
                price = float(price_key)
                # Liegt dieser Level innerhalb des Grid-Bereichs?
                in_range = gc.get('lower_price', 0) <= price <= gc.get('upper_price', 1e12)
                marker = '' if in_range else ' [!]'
                print(f"  {price:>15,.4f}  {side:<6}  {oid:<30}  {placed}{marker}")

        buy_count = sum(1 for o in orders.values() if o.get('side') == 'buy')
        sell_count = sum(1 for o in orders.values() if o.get('side') == 'sell')
        print(f"\n  Summe: {buy_count} Buy-Orders, {sell_count} Sell-Orders")
    sep('-')


# ---------------------------------------------------------------------------
# Modus 3: Performance & PnL
# ---------------------------------------------------------------------------

def mode_3_performance():
    header("Modus 3 — Performance & PnL Analyse")

    trackers = load_trackers()
    if not trackers:
        print("\n  Keine Tracker-Dateien gefunden.")
        return

    total_pnl = 0.0
    total_fills = 0

    for t in trackers:
        symbol = t.get('symbol', 'Unbekannt')
        gc = t.get('grid_config', {})
        perf = t.get('performance', {})
        init_at = t.get('initialized_at', '—')

        pnl = perf.get('realized_pnl_usdt', 0.0)
        fees = perf.get('fee_paid_usdt', 0.0)
        fills = perf.get('total_fills', 0)
        buy_fills = perf.get('buy_fills', 0)
        sell_fills = perf.get('sell_fills', 0)
        last_fill = perf.get('last_fill_at', '—')
        investment = gc.get('total_investment_usdt', 1.0) or 1.0

        # ROI
        roi_pct = (pnl / investment) * 100

        # Durchschnittlicher Gewinn pro Zyklus
        completed_cycles = sell_fills  # Jeder Sell = ein abgeschlossener Kauf-Verkauf-Zyklus
        avg_pnl_per_cycle = (pnl / completed_cycles) if completed_cycles > 0 else 0.0

        print(f"\n  Symbol          : {symbol}")
        print(f"  Gestartet       : {init_at[:19] if init_at else '—'}")
        print(f"  Grid-Modus      : {gc.get('mode','?').upper()}")
        print(f"  Kapital         : {investment:.2f} USDT ({gc.get('leverage','?')}x Hebel)")
        print()
        print(f"  Fills gesamt    : {fills}")
        print(f"    Buy-Fills     : {buy_fills}")
        print(f"    Sell-Fills    : {sell_fills} (abgeschlossene Zyklen)")
        print(f"  Letzter Fill    : {last_fill[:19] if last_fill and last_fill != '—' else '—'}")
        print()
        pnl_str = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
        roi_str = f"+{roi_pct:.3f}%" if roi_pct >= 0 else f"{roi_pct:.3f}%"
        print(f"  Realized PnL    : {pnl_str} USDT")
        print(f"  ROI             : {roi_str}")
        print(f"  Gezahlte Fees   : {fees:.4f} USDT")
        print(f"  PnL/Zyklus (Ø)  : {avg_pnl_per_cycle:.4f} USDT")

        total_pnl += pnl
        total_fills += fills

    sep('-')
    print(f"\n  GESAMT-PnL alle Grids : {'+' if total_pnl >= 0 else ''}{total_pnl:.4f} USDT")
    print(f"  GESAMT-Fills          : {total_fills}")
    sep('-')


# ---------------------------------------------------------------------------
# Modus 4: Fibonacci-Analyse
# ---------------------------------------------------------------------------

def mode_4_fibonacci():
    header("Modus 4 — Fibonacci-Analyse (aktueller Range)")

    configs = load_configs()
    if not configs:
        print("\n  Keine Config-Dateien gefunden.")
        print("  Starte zuerst ./run_pipeline.sh um Symbole zu konfigurieren.")
        return

    try:
        from gbot.analysis.fibonacci import auto_fib_analysis
    except ImportError as e:
        print(f"\n  Fehler: fibonacci-Modul nicht verfuegbar: {e}")
        return

    for filename, cfg in configs:
        sym = cfg.get('market', {}).get('symbol', '?')
        fib_cfg = cfg.get('grid', {}).get('fibonacci', {})
        timeframe = fib_cfg.get('timeframe', '4h')
        lookback = fib_cfg.get('lookback', 200)
        swing_window = fib_cfg.get('swing_window', 10)
        prefer_golden = fib_cfg.get('prefer_golden_zone', False)

        print(f"\n  Symbol: {sym} | Zeitfenster: {timeframe} | Lookback: {lookback} Kerzen")
        print("  " + "-" * 56)
        try:
            analysis = auto_fib_analysis(
                symbol=sym,
                timeframe=timeframe,
                lookback=lookback,
                swing_window=swing_window,
                prefer_golden_zone=prefer_golden,
            )
            swing = analysis['swing_points']
            fib = analysis['fib_levels']
            suggested = analysis['suggested_range']
            current = analysis['current_price']

            print(f"  Aktueller Preis : {current:,.4f}")
            print(f"  Swing High      : {swing['swing_high']:,.4f}")
            print(f"  Swing Low       : {swing['swing_low']:,.4f}")
            print(f"  Trend           : {swing['trend'].upper()}")
            print(f"  In Goldener Zone: {'JA' if suggested['in_golden_zone'] else 'NEIN'}")
            print()
            print(f"  Vorgeschlagener Grid-Bereich:")
            print(f"    Unten ({suggested['lower_label']}): {suggested['lower_price']:,.4f}")
            print(f"    Oben  ({suggested['upper_label']}): {suggested['upper_price']:,.4f}")
            width = suggested['upper_price'] - suggested['lower_price']
            width_pct = width / current * 100 if current > 0 else 0
            print(f"    Breite: {width:,.4f} ({width_pct:.2f}% vom Preis)")

        except Exception as e:
            print(f"  Fehler bei Fibonacci-Analyse: {e}")

    sep('-')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="gbot Analyse-Tool")
    parser.add_argument('--mode', type=int, default=None, help="Modus 1-4")
    args = parser.parse_args()

    mode = args.mode
    if mode is None:
        print("\nWaehle einen Analyse-Modus:")
        print("  1) Grid Status Uebersicht")
        print("  2) Order-Analyse nach Grid-Levels")
        print("  3) Performance & PnL Analyse")
        print("  4) Fibonacci-Analyse (aktueller Range fuer alle Symbole)")
        try:
            raw = input("Auswahl (1-4) [Standard: 1]: ").strip()
            mode = int(raw) if raw else 1
        except (ValueError, EOFError):
            mode = 1

    modes = {1: mode_1_status, 2: mode_2_orders, 3: mode_3_performance, 4: mode_4_fibonacci}
    func = modes.get(mode)
    if func:
        func()
    else:
        print(f"Ungueltiger Modus: {mode}. Bitte 1-4 eingeben.")
        sys.exit(1)


if __name__ == '__main__':
    main()
