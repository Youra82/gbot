# src/gbot/analysis/optimizer.py
"""
Grid Trading Optimizer — findet die besten num_grids und leverage via Optuna.

Ablauf:
  1. OHLCV-Daten laden (oeffentlicher Endpoint, kein API-Key)
  2. Fibonacci Retracement → Grid-Bereich automatisch bestimmen
  3. Optuna-Optimierung: teste verschiedene (num_grids, leverage)-Kombinationen
  4. Bestes Ergebnis als Config speichern

Parameter die IMMER fix gesetzt werden (kein Optimizer noetig):
  - grid_mode   : neutral  (Buy + Sell — handelt beide Richtungen)
  - margin_mode : isolated
  - fibonacci   : enabled = true (Live-Bot berechnet immer fresh)

Verwendung als Skript (fuer run_pipeline.sh):
  python3 optimizer.py --symbol BTC/USDT:USDT --timeframe 4h
                       --capital 100 --trials 50 --max_drawdown 50
"""

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'gbot', 'strategy', 'configs')
RESULTS_DIR = os.path.join(PROJECT_ROOT, 'artifacts', 'results')

# Lookback-Kerzen pro Zeitfenster (Backtest + Fibonacci)
LOOKBACK_BY_TF = {
    '1m': 500, '3m': 500, '5m': 500, '15m': 500,
    '30m': 500, '1h': 500, '2h': 500, '4h': 500,
    '6h': 365, '8h': 365, '12h': 365, '1d': 365,
}
DEFAULT_LOOKBACK = 500

# Suchraum fuer den Optimizer
NUM_GRIDS_MIN = 5
NUM_GRIDS_MAX = 25
LEVERAGE_MIN = 1
LEVERAGE_MAX = 10

# Fibonacci-Parameter (fest — wie Live-Bot)
FIB_SWING_WINDOW = 10
FIB_PREFER_GOLDEN_ZONE = False
FIB_REBALANCE_ON_BREAK = True
FIB_MIN_REBALANCE_HOURS = 4


# ---------------------------------------------------------------------------
# Kern-Optimierung
# ---------------------------------------------------------------------------

def run_optimization(
    symbol: str,
    timeframe: str,
    capital: float,
    n_trials: int = 50,
    max_drawdown: float = 50.0,
    lookback: int = None,
    start_date: str = None,
    end_date: str = None,
    n_jobs: int = -1,
    mode: str = 'strict',
) -> dict:
    """
    Fuehrt die vollstaendige Optimizer-Pipeline durch.

    Args:
        start_date : Backtest-Startdatum 'JJJJ-MM-TT' (optional, filtert df)
        end_date   : Backtest-Enddatum  'JJJJ-MM-TT' (optional)
        n_jobs     : Optuna parallele Jobs (-1 = alle Kerne)
        mode       : 'strict' (max_drawdown-Limit aktiv) | 'best_profit' (kein Limit)

    Returns:
        dict mit best_params, best_result, fib_analysis
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        raise ImportError("optuna ist nicht installiert. Bitte 'pip install optuna' ausfuehren.")

    from gbot.analysis.fibonacci import auto_fib_analysis, fetch_ohlcv_public
    from gbot.analysis.backtester import run_grid_backtest

    if lookback is None:
        lookback = LOOKBACK_BY_TF.get(timeframe, DEFAULT_LOOKBACK)

    # 1. Daten laden
    print(f"  Lade OHLCV-Daten: {symbol} ({timeframe}, {lookback} Kerzen)...")
    df = fetch_ohlcv_public(symbol, timeframe, lookback)
    print(f"  {len(df)} Kerzen geladen.")

    # 2. Datum filtern
    if start_date:
        df = df[df.index >= start_date]
        print(f"  Gefiltert ab    : {start_date}")
    if end_date:
        df = df[df.index <= end_date]
        print(f"  Gefiltert bis   : {end_date}")
    print(f"  Backtest-Kerzen : {len(df)}")

    if len(df) < 10:
        raise ValueError(f"Zu wenige Kerzen nach Datumsfilter ({len(df)}). Startdatum anpassen.")

    # 3. Fibonacci-Range bestimmen (auf den letzten 200 Kerzen)
    fib_lookback = min(200, len(df))
    print(f"  Berechne Fibonacci Retracement ({fib_lookback} Kerzen)...")
    analysis = auto_fib_analysis(
        symbol=symbol,
        timeframe=timeframe,
        lookback=fib_lookback,
        swing_window=FIB_SWING_WINDOW,
        prefer_golden_zone=FIB_PREFER_GOLDEN_ZONE,
    )
    lower = analysis['suggested_range']['lower_price']
    upper = analysis['suggested_range']['upper_price']
    lower_label = analysis['suggested_range']['lower_label']
    upper_label = analysis['suggested_range']['upper_label']
    current_price = analysis['current_price']
    swing = analysis['swing_points']

    print(f"  Fibonacci-Range : {lower:.4f} ({lower_label}) - {upper:.4f} ({upper_label})")
    print(f"  Swing High/Low  : {swing['swing_high']:.4f} / {swing['swing_low']:.4f}")
    print(f"  Trend           : {swing['trend'].upper()}")
    print(f"  Aktueller Preis : {current_price:.4f}")
    dd_info = f"Max DD: {max_drawdown}%" if mode == 'strict' else "kein Drawdown-Limit"
    print(f"  Modus           : {mode} ({dd_info})")
    print(f"  Optimiere       : {n_trials} Trials | num_grids {NUM_GRIDS_MIN}-{NUM_GRIDS_MAX} | "
          f"leverage {LEVERAGE_MIN}-{LEVERAGE_MAX}")

    # 4. Optuna-Studie
    def objective(trial):
        num_grids = trial.suggest_int('num_grids', NUM_GRIDS_MIN, NUM_GRIDS_MAX)
        leverage = trial.suggest_int('leverage', LEVERAGE_MIN, LEVERAGE_MAX)

        result = run_grid_backtest(
            df=df,
            lower=lower,
            upper=upper,
            num_grids=num_grids,
            leverage=leverage,
            capital=capital,
        )

        if result.get('error'):
            return -9999.0
        if mode == 'strict' and result['max_drawdown_pct'] > max_drawdown:
            return -9999.0

        return result['roi_pct']

    study = optuna.create_study(direction='maximize')
    study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=True)

    if study.best_value <= -9999.0:
        raise ValueError(
            f"Kein gueltiges Ergebnis gefunden. Drawdown-Limit ({max_drawdown}%) zu streng "
            f"oder Kapital zu gering. Modus 2 oder hoehere Trials versuchen."
        )

    # 5. Bestes Ergebnis
    best_params = study.best_params
    best_result = run_grid_backtest(
        df=df,
        lower=lower,
        upper=upper,
        num_grids=best_params['num_grids'],
        leverage=best_params['leverage'],
        capital=capital,
    )

    return {
        'symbol': symbol,
        'timeframe': timeframe,
        'capital': capital,
        'lookback': lookback,
        'fib_lookback': fib_lookback,
        'start_date': start_date,
        'end_date': end_date,
        'mode': mode,
        'num_grids': best_params['num_grids'],
        'leverage': best_params['leverage'],
        'roi_pct': best_result.get('roi_pct', 0),
        'max_drawdown_pct': best_result.get('max_drawdown_pct', 0),
        'total_fills': best_result.get('total_fills', 0),
        'total_pnl_usdt': best_result.get('total_pnl_usdt', 0),
        'spacing': best_result.get('spacing', 0),
        'amount_per_grid': best_result.get('amount_per_grid', 0),
        'lower_price': lower,
        'upper_price': upper,
        'lower_label': lower_label,
        'upper_label': upper_label,
        'fib_analysis': analysis,
    }


# ---------------------------------------------------------------------------
# Config schreiben
# ---------------------------------------------------------------------------

def write_config(result: dict, settings_file: str = None) -> str:
    """
    Schreibt die beste Konfiguration als JSON-Datei.
    Gibt den Pfad zur Config-Datei zurueck.
    """
    symbol = result['symbol']
    safe = symbol.replace('/', '_').replace(':', '')
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    config_path = os.path.join(CONFIGS_DIR, f"config_{safe}.json")

    config = {
        "market": {
            "symbol": symbol,
        },
        "grid": {
            "num_grids": result['num_grids'],
            "grid_mode": "neutral",
            "fibonacci": {
                "enabled": True,
                "timeframe": result['timeframe'],
                "lookback": 200,
                "swing_window": FIB_SWING_WINDOW,
                "prefer_golden_zone": FIB_PREFER_GOLDEN_ZONE,
                "rebalance_on_break": FIB_REBALANCE_ON_BREAK,
                "min_rebalance_interval_hours": FIB_MIN_REBALANCE_HOURS,
            },
        },
        "risk": {
            "total_investment_usdt": result['capital'],
            "leverage": result['leverage'],
            "margin_mode": "isolated",
        },
    }

    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)

    # settings.json aktualisieren
    if settings_file and os.path.exists(settings_file):
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
            strategies = settings.setdefault('live_trading_settings', {}).setdefault('active_strategies', [])
            if not any(s.get('symbol') == symbol for s in strategies):
                strategies.append({'symbol': symbol, 'active': True})
                with open(settings_file, 'w') as f:
                    json.dump(settings, f, indent=4)
        except Exception as e:
            logger.warning(f"settings.json konnte nicht aktualisiert werden: {e}")

    return config_path


# ---------------------------------------------------------------------------
# Ergebnis ausgeben
# ---------------------------------------------------------------------------

def print_result(result: dict):
    """Gibt das Optimierungsergebnis formatiert aus."""
    w = 60
    symbol = result['symbol']
    tf = result['timeframe']

    print('=' * w)
    print(f"  Optimierungsergebnis — {symbol} ({tf})")
    print('=' * w)
    start = result.get('start_date') or 'auto'
    end = result.get('end_date') or 'heute'
    mode = result.get('mode', 'strict')
    print(f"  Beste Parameter:")
    print(f"    num_grids  : {result['num_grids']}")
    print(f"    leverage   : {result['leverage']}x")
    print(f"    grid_mode  : neutral (immer)")
    print(f"    margin     : isolated (immer)")
    print(f"    Modus      : {mode}")
    print('-' * w)
    print(f"  Fibonacci-Range (aktuell):")
    print(f"    Unten  ({result['lower_label']}): {result['lower_price']:,.4f}")
    print(f"    Oben   ({result['upper_label']}): {result['upper_price']:,.4f}")
    print(f"    Abstand: {result['spacing']:.4f} pro Stufe")
    print('-' * w)
    print(f"  Backtest ({start} bis {end}):")
    print(f"    ROI             : {result['roi_pct']:+.2f}%")
    print(f"    Max Drawdown    : {result['max_drawdown_pct']:.2f}%")
    print(f"    Gesamt-Fills    : {result['total_fills']}")
    print(f"    Gesamt-PnL      : {result['total_pnl_usdt']:+.4f} USDT")
    if result['total_fills'] > 0:
        avg = result['total_pnl_usdt'] / result['total_fills']
        print(f"    Ø PnL / Fill   : {avg:+.4f} USDT")
    print(f"    Kapital         : {result['capital']} USDT")
    print('=' * w)


# ---------------------------------------------------------------------------
# CLI-Modus (fuer run_pipeline.sh)
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="gbot Grid Optimizer")
    parser.add_argument('--symbol', required=True, help="Symbol z.B. BTC/USDT:USDT")
    parser.add_argument('--timeframe', default='4h', help="Zeitrahmen z.B. 4h")
    parser.add_argument('--capital', type=float, default=100.0, help="Kapital in USDT")
    parser.add_argument('--trials', type=int, default=50, help="Anzahl Optuna-Trials")
    parser.add_argument('--max_drawdown', type=float, default=50.0, help="Max Drawdown %")
    parser.add_argument('--lookback', type=int, default=None, help="Anzahl Kerzen (auto wenn leer)")
    parser.add_argument('--start_date', default=None, help="Backtest-Start JJJJ-MM-TT")
    parser.add_argument('--end_date', default=None, help="Backtest-Ende JJJJ-MM-TT")
    parser.add_argument('--jobs', type=int, default=-1, help="CPU-Kerne fuer Optuna (-1 = alle)")
    parser.add_argument('--mode', default='strict', choices=['strict', 'best_profit'],
                        help="strict = Drawdown-Limit, best_profit = kein Limit")
    parser.add_argument('--settings', default=None, help="Pfad zur settings.json")
    parser.add_argument('--no_save', action='store_true', help="Config NICHT speichern")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

    try:
        result = run_optimization(
            symbol=args.symbol,
            timeframe=args.timeframe,
            capital=args.capital,
            n_trials=args.trials,
            max_drawdown=args.max_drawdown,
            lookback=args.lookback,
            start_date=args.start_date,
            end_date=args.end_date,
            n_jobs=args.jobs,
            mode=args.mode,
        )
    except Exception as e:
        print(f"\nFehler beim Optimieren von {args.symbol}: {e}", file=sys.stderr)
        sys.exit(1)

    print_result(result)

    if not args.no_save:
        settings_path = args.settings or os.path.join(PROJECT_ROOT, 'settings.json')
        config_path = write_config(result, settings_file=settings_path)
        print(f"\n  Config gespeichert: {config_path}")
    else:
        print("\n  (Config nicht gespeichert — --no_save aktiv)")


if __name__ == '__main__':
    main()
