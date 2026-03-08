# src/gbot/analysis/show_results.py
"""
Analyse- und Anzeige-Skript fuer den gbot.

Modi:
  1) Einzel-Analyse            — jede Grid-Strategie wird isoliert getestet
  2) Manuelle Portfolio-Sim    — du waehlst das Team
  3) Auto Portfolio-Optimierung— der Bot waehlt das beste Team
  4) Interaktive Charts        — Fibonacci-Zonen + Grid-Levels
"""
import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, timezone
from itertools import combinations

import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'gbot', 'strategy', 'configs')
SETTINGS_FILE = os.path.join(PROJECT_ROOT, 'settings.json')


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def sep(char='=', width=68):
    print(char * width)


def load_configs() -> list:
    files = sorted(glob.glob(os.path.join(CONFIGS_DIR, 'config_*.json')))
    configs = []
    for path in files:
        try:
            with open(path) as f:
                configs.append((os.path.basename(path), json.load(f)))
        except Exception as e:
            print(f"  [WARNUNG] Config nicht lesbar ({os.path.basename(path)}): {e}")
    return configs


def _fetch_and_backtest(cfg: dict, start_date: str, end_date: str, capital: float) -> dict:
    """
    Laedt OHLCV-Daten, berechnet Fibonacci-Range und fuehrt den Backtest durch.
    Gibt Backtest-Ergebnis + Fibonacci-Info zurueck oder {'error': ...}.
    """
    from gbot.analysis.fibonacci import fetch_ohlcv_public, auto_fib_analysis
    from gbot.analysis.backtester import run_grid_backtest

    sym = cfg['market']['symbol']
    fib_cfg = cfg['grid'].get('fibonacci', {})
    tf = fib_cfg.get('timeframe', '4h')
    lookback_fib = fib_cfg.get('lookback', 200)
    num_grids = cfg['grid']['num_grids']
    leverage = cfg['risk'].get('leverage', 1)

    # OHLCV fuer Backtest-Zeitraum laden
    try:
        from gbot.analysis.optimizer import LOOKBACK_BY_TF, DEFAULT_LOOKBACK
        lookback_full = LOOKBACK_BY_TF.get(tf, DEFAULT_LOOKBACK)
        df = fetch_ohlcv_public(sym, tf, lookback_full)
        df = df[df.index >= start_date]
        df = df[df.index <= end_date]
        if len(df) < 10:
            return {'error': f'Zu wenige Kerzen ({len(df)}) im Zeitraum'}
    except Exception as e:
        return {'error': f'Datenabruf fehlgeschlagen: {e}'}

    # Fibonacci-Range aus aktuellen Daten
    try:
        analysis = auto_fib_analysis(
            symbol=sym,
            timeframe=tf,
            lookback=lookback_fib,
            swing_window=fib_cfg.get('swing_window', 10),
            prefer_golden_zone=fib_cfg.get('prefer_golden_zone', False),
        )
        lower = analysis['suggested_range']['lower_price']
        upper = analysis['suggested_range']['upper_price']
        lower_label = analysis['suggested_range']['lower_label']
        upper_label = analysis['suggested_range']['upper_label']
    except Exception as e:
        return {'error': f'Fibonacci fehlgeschlagen: {e}'}

    result = run_grid_backtest(
        df=df,
        lower=lower,
        upper=upper,
        num_grids=num_grids,
        leverage=leverage,
        capital=capital,
    )

    result['symbol'] = sym
    result['timeframe'] = tf
    result['lower'] = lower
    result['upper'] = upper
    result['lower_label'] = lower_label
    result['upper_label'] = upper_label
    result['candles'] = len(df)
    result['fib_analysis'] = analysis
    return result


# ---------------------------------------------------------------------------
# Modus 1: Einzel-Analyse
# ---------------------------------------------------------------------------

def run_single_analysis(start_date: str, end_date: str, capital: float):
    sep()
    print("  gbot Einzel-Analyse — jede Strategie isoliert getestet")
    sep()
    print(f"  Zeitraum: {start_date} bis {end_date} | Gesamtkapital: {capital} USDT\n")

    configs = load_configs()
    if not configs:
        print("  Keine Config-Dateien gefunden. Bitte ./run_pipeline.sh ausfuehren.")
        return

    all_results = []
    for filename, cfg in configs:
        sym = cfg['market']['symbol']
        print(f"  Analysiere: {sym} ...", end=' ', flush=True)
        r = _fetch_and_backtest(cfg, start_date, end_date, capital)
        if r.get('error'):
            print(f"FEHLER: {r['error']}")
            continue
        print(f"OK ({r['candles']} Kerzen)")
        all_results.append({
            'Strategie': f"{sym} ({r['timeframe']})",
            'Grids': r['num_grids'],
            'Hebel': f"{r['leverage']}x",
            'Fills': r['total_fills'],
            'ROI %': r['roi_pct'],
            'Max DD %': r['max_drawdown_pct'],
            'PnL USDT': r['total_pnl_usdt'],
            'Range': f"{r['lower_label']}–{r['upper_label']}",
        })

    if not all_results:
        print("\n  Keine gueltigen Ergebnisse.")
        return

    df = pd.DataFrame(all_results).sort_values('ROI %', ascending=False)
    pd.set_option('display.width', 140)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.float_format', '{:.2f}'.format)
    sep()
    print(df.to_string(index=False))
    sep()


# ---------------------------------------------------------------------------
# Modus 2: Manuelle Portfolio-Simulation
# ---------------------------------------------------------------------------

def run_manual_portfolio(start_date: str, end_date: str, capital: float):
    sep()
    print("  gbot Manuelle Portfolio-Simulation")
    sep()

    configs = load_configs()
    if not configs:
        print("  Keine Config-Dateien gefunden.")
        return

    print("\n  Verfuegbare Strategien:")
    for i, (filename, cfg) in enumerate(configs):
        sym = cfg['market']['symbol']
        tf = cfg['grid'].get('fibonacci', {}).get('timeframe', '?')
        print(f"    {i+1}) {sym} ({tf})")

    selection = input("\n  Welche Strategien simulieren? (Zahlen mit Komma, z.B. 1,3 oder 'alle'): ").strip()
    if selection.lower() == 'alle':
        selected = configs
    else:
        try:
            indices = [int(x.strip()) - 1 for x in selection.split(',')]
            selected = [configs[i] for i in indices]
        except (ValueError, IndexError):
            print("  Ungueltige Auswahl.")
            return

    print(f"\n  Zeitraum: {start_date} bis {end_date} | Gesamtkapital: {capital} USDT\n")

    results = []
    total_capital = capital * len(selected)
    total_pnl = 0.0
    total_fills = 0

    for filename, cfg in selected:
        sym = cfg['market']['symbol']
        print(f"  Backtest: {sym} ...", end=' ', flush=True)
        r = _fetch_and_backtest(cfg, start_date, end_date, capital)
        if r.get('error'):
            print(f"FEHLER: {r['error']}")
            continue
        print(f"ROI: {r['roi_pct']:+.2f}%")
        results.append(r)
        total_pnl += r['total_pnl_usdt']
        total_fills += r['total_fills']

    if not results:
        print("\n  Keine gueltigen Ergebnisse.")
        return

    portfolio_roi = (total_pnl / total_capital * 100) if total_capital > 0 else 0
    max_dd = max(r['max_drawdown_pct'] for r in results)

    sep()
    print("  Portfolio-Simulations-Ergebnis")
    sep('-')
    for r in results:
        pnl_str = f"{r['total_pnl_usdt']:+.4f}"
        print(f"  {r['symbol']:<22} ROI: {r['roi_pct']:>+7.2f}%  DD: {r['max_drawdown_pct']:>5.2f}%  PnL: {pnl_str} USDT  Fills: {r['total_fills']}")
    sep('-')
    print(f"  Gesamt-Kapital   : {total_capital:.2f} USDT")
    print(f"  Gesamt-PnL       : {total_pnl:+.4f} USDT")
    print(f"  Portfolio ROI    : {portfolio_roi:+.2f}%")
    print(f"  Hoechster Max DD : {max_dd:.2f}%")
    print(f"  Gesamt-Fills     : {total_fills}")
    sep()


# ---------------------------------------------------------------------------
# Modus 3: Automatische Portfolio-Optimierung
# ---------------------------------------------------------------------------

def run_auto_portfolio(start_date: str, end_date: str, capital: float, target_max_dd: float):
    sep()
    print("  gbot Automatische Portfolio-Optimierung")
    sep()
    print(f"  Ziel: Maximaler ROI bei maximal {target_max_dd:.1f}% Drawdown pro Grid.")
    print(f"  Zeitraum: {start_date} bis {end_date} | Gesamtkapital: {capital} USDT\n")

    configs = load_configs()
    if not configs:
        print("  Keine Config-Dateien gefunden.")
        return

    # Schritt 1: Alle Configs einzeln backtesten
    individual = []
    for filename, cfg in configs:
        sym = cfg['market']['symbol']
        print(f"  Backtest: {sym} ...", end=' ', flush=True)
        r = _fetch_and_backtest(cfg, start_date, end_date, capital)
        if r.get('error'):
            print(f"FEHLER: {r['error']}")
            continue
        print(f"ROI: {r['roi_pct']:+.2f}%  DD: {r['max_drawdown_pct']:.2f}%")
        individual.append((filename, cfg, r))

    if not individual:
        print("\n  Keine gueltigen Ergebnisse.")
        return

    # Schritt 2: Beste Kombination finden (bis 5er-Teams)
    best_roi = -9999.0
    best_team = []
    best_stats = {}

    max_team_size = min(5, len(individual))
    total_combos = sum(len(list(combinations(individual, k))) for k in range(1, max_team_size + 1))
    print(f"\n  Pruefe {total_combos} Portfolio-Kombinationen...")

    for size in range(1, max_team_size + 1):
        for combo in combinations(individual, size):
            valid = all(r['max_drawdown_pct'] <= target_max_dd for _, _, r in combo)
            valid = valid and all(r['roi_pct'] > -9999 for _, _, r in combo)
            if not valid:
                continue
            total_pnl = sum(r['total_pnl_usdt'] for _, _, r in combo)
            total_cap = capital * size
            roi = total_pnl / total_cap * 100
            max_dd = max(r['max_drawdown_pct'] for _, _, r in combo)
            if roi > best_roi:
                best_roi = roi
                best_team = combo
                best_stats = {
                    'total_pnl': total_pnl,
                    'total_capital': total_cap,
                    'roi': roi,
                    'max_dd': max_dd,
                    'fills': sum(r['total_fills'] for _, _, r in combo),
                }

    sep()
    if not best_team:
        print(f"  Kein Portfolio gefunden, das Max Drawdown <= {target_max_dd:.1f}% erfuellt.")
        print("  Versuche einen hoeher Drawdown-Wert oder mehr Trials.")
        sep()
        return

    print(f"  Optimales Team gefunden ({len(best_team)} Strategien):")
    sep('-')
    for filename, cfg, r in best_team:
        sym = r['symbol']
        print(f"  {sym:<22}  ROI: {r['roi_pct']:>+7.2f}%  DD: {r['max_drawdown_pct']:>5.2f}%  Fills: {r['total_fills']}")
    sep('-')
    print(f"  Gesamt-Kapital   : {best_stats['total_capital']:.2f} USDT")
    print(f"  Gesamt-PnL       : {best_stats['total_pnl']:+.4f} USDT")
    print(f"  Portfolio ROI    : {best_stats['roi']:+.2f}%")
    print(f"  Hoechster Max DD : {best_stats['max_dd']:.2f}%")
    print(f"  Gesamt-Fills     : {best_stats['fills']}")
    sep()

    # Ergebnis speichern fuer Shell-Nachbearbeitung
    opt_result = {
        'optimal_portfolio': [filename for filename, _, _ in best_team],
        'stats': best_stats,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
    results_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, 'optimization_results.json')
    with open(out_path, 'w') as f:
        json.dump(opt_result, f, indent=2)
    print(f"  Ergebnis gespeichert: {out_path}")


# ---------------------------------------------------------------------------
# Modus 4: Interaktive Charts (Plotly HTML)
# ---------------------------------------------------------------------------

def run_interactive_charts():
    try:
        from gbot.analysis.interactive_charts import main as charts_main
        charts_main()
    except Exception as e:
        print(f"\n  Fehler beim Ausfuehren der interaktiven Charts: {e}")
        import traceback
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="gbot Analyse-Tool")
    parser.add_argument('--mode', type=str, default='1', choices=['1', '2', '3', '4'])
    parser.add_argument('--start_date', type=str, default=None)
    parser.add_argument('--end_date', type=str, default=None)
    parser.add_argument('--capital', type=float, default=None)
    parser.add_argument('--target_max_drawdown', type=float, default=30.0)
    args = parser.parse_args()

    mode = args.mode

    # Modus 4 hat eigenes Eingabe-System
    if mode == '4':
        run_interactive_charts()
        return

    # Datum und Kapital abfragen
    start_date = args.start_date or input("  Startdatum (JJJJ-MM-TT) [Standard: 2023-01-01]: ").strip() or "2023-01-01"
    end_date = args.end_date or input(f"  Enddatum   (JJJJ-MM-TT) [Standard: Heute]:      ").strip() or date.today().strftime("%Y-%m-%d")
    cap_input = args.capital
    if cap_input is None:
        raw = input("  Gesamtkapital in USDT          [Standard: 100]:   ").strip()
        cap_input = float(raw) if raw else 100.0

    print()

    if mode == '1':
        run_single_analysis(start_date, end_date, cap_input)
    elif mode == '2':
        run_manual_portfolio(start_date, end_date, cap_input)
    elif mode == '3':
        run_auto_portfolio(start_date, end_date, cap_input, args.target_max_drawdown)


if __name__ == '__main__':
    main()
