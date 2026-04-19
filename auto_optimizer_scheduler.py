#!/usr/bin/env python3
"""
auto_optimizer_scheduler.py

Prueft bei jedem Aufruf ob eine Optimierung faellig ist und fuehrt
die Grid-Optimierung fuer die in settings.json definierten Strategien aus.
Sendet Telegram-Benachrichtigungen bei Start und Ende.

Aufruf:
  python3 auto_optimizer_scheduler.py           # normale Pruefung
  python3 auto_optimizer_scheduler.py --force   # sofort erzwingen
"""

import os
import sys
import json
import time
import subprocess
import argparse
from datetime import datetime, date, timedelta

PROJECT_ROOT     = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

CACHE_DIR        = os.path.join(PROJECT_ROOT, 'data', 'cache')
LOG_DIR          = os.path.join(PROJECT_ROOT, 'logs')
SETTINGS_FILE    = os.path.join(PROJECT_ROOT, 'settings.json')
OPTIMIZER_SCRIPT = os.path.join(PROJECT_ROOT, 'src', 'gbot', 'analysis', 'optimizer.py')
SECRET_FILE      = os.path.join(PROJECT_ROOT, 'secret.json')
LAST_RUN_FILE    = os.path.join(CACHE_DIR, '.last_optimization_run')
IN_PROGRESS_FILE = os.path.join(CACHE_DIR, '.optimization_in_progress')
TRIGGER_LOG      = os.path.join(LOG_DIR, 'auto_optimizer_trigger.log')
OPTIMIZER_RESULTS_FILE = os.path.join(
    PROJECT_ROOT, 'artifacts', 'results', 'last_optimizer_run.json')

LOOKBACK_MAP = {
    '5m': 60,  '15m': 60,
    '30m': 365, '1h': 365,
    '2h': 730,  '4h': 730,
    '6h': 1095, '1d': 1095,
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(msg: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"{datetime.now().isoformat()} AUTO-OPTIMIZER {msg}"
    with open(TRIGGER_LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _format_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def _get_last_run() -> datetime | None:
    if not os.path.exists(LAST_RUN_FILE):
        return None
    with open(LAST_RUN_FILE, 'r') as f:
        s = f.read().strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _set_last_run():
    os.makedirs(CACHE_DIR, exist_ok=True)
    now_str = datetime.now().isoformat()
    with open(LAST_RUN_FILE, 'w') as f:
        f.write(now_str)
    _log(f"LAST_RUN updated={now_str}")


def _is_due(schedule: dict) -> tuple[bool, str]:
    if os.path.exists(IN_PROGRESS_FILE):
        _log("SKIP already_in_progress")
        return False, None

    last_run = _get_last_run()
    if last_run is None:
        return True, 'forced'

    interval_cfg     = schedule.get('interval', {})
    value            = int(interval_cfg.get('value', 7))
    unit             = interval_cfg.get('unit', 'days')
    multipliers      = {'minutes': 60, 'hours': 3600, 'days': 86400, 'weeks': 604800}
    interval_seconds = value * multipliers.get(unit, 86400)

    if (datetime.now() - last_run).total_seconds() >= interval_seconds:
        return True, 'interval'

    now    = datetime.now()
    dow    = int(schedule.get('day_of_week', 6))
    hour   = int(schedule.get('hour', 2))
    minute = int(schedule.get('minute', 0))
    if now.weekday() == dow and now.hour == hour and minute <= now.minute < minute + 15:
        if last_run.date() < now.date():
            return True, 'scheduled'

    return False, None


# ---------------------------------------------------------------------------
# Paare aufloesen
# ---------------------------------------------------------------------------

def _resolve_pairs_auto(live_settings: dict) -> list:
    """Liest exakte (symbol, timeframe) Paare aus active_strategies."""
    pairs, seen = [], set()
    for s in live_settings.get('active_strategies', []):
        if not s.get('active', True):
            continue
        sym = s.get('symbol', '')
        tf  = s.get('timeframe', '')
        if sym and tf and (sym, tf) not in seen:
            pairs.append((sym, tf))
            seen.add((sym, tf))
    return pairs or [('BTC/USDT:USDT', '1h')]


def _resolve_pairs(opt_settings: dict, live_settings: dict) -> list:
    symbols_cfg    = opt_settings.get('symbols_to_optimize', 'auto')
    timeframes_cfg = opt_settings.get('timeframes_to_optimize', 'auto')

    if symbols_cfg == 'auto':
        return _resolve_pairs_auto(live_settings)

    symbols    = symbols_cfg if isinstance(symbols_cfg, list) else [symbols_cfg]
    timeframes = timeframes_cfg if isinstance(timeframes_cfg, list) else [timeframes_cfg]
    return [(f"{sym}/USDT:USDT", tf) for sym in symbols for tf in timeframes]


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _get_telegram_credentials():
    try:
        with open(SECRET_FILE, 'r') as f:
            secrets = json.load(f)
        tg = secrets.get('telegram', {})
        return tg.get('bot_token'), tg.get('chat_id')
    except Exception:
        return None, None


def _send_telegram(message: str):
    bot_token, chat_id = _get_telegram_credentials()
    if not bot_token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={'chat_id': chat_id, 'text': message},
            timeout=10,
        )
        _log("TELEGRAM sent")
    except Exception as e:
        _log(f"TELEGRAM ERROR {e}")


def _send_start_telegram(pair_display: list, num_trials: int, start_time: datetime):
    _send_telegram(
        f"\U0001f680 gbot Auto-Optimizer GESTARTET\n"
        f"Paare: {', '.join(pair_display)}\n"
        f"Trials: {num_trials}\n"
        f"Start: {start_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _send_end_telegram(elapsed_seconds: float):
    dur = _format_elapsed(elapsed_seconds)
    if not os.path.exists(OPTIMIZER_RESULTS_FILE):
        _send_telegram(f"\u2705 gbot Auto-Optimizer abgeschlossen\nDauer: {dur}")
        return
    try:
        with open(OPTIMIZER_RESULTS_FILE, encoding='utf-8') as f:
            results = json.load(f)
    except Exception:
        _send_telegram(f"\u2705 gbot Auto-Optimizer abgeschlossen (Dauer: {dur})")
        return

    saved  = results.get('saved', [])
    failed = results.get('failed', [])
    total  = len(saved) + len(failed)
    lines  = [f"\u2705 gbot Auto-Optimizer abgeschlossen (Dauer: {dur})"]
    if saved:
        lines.append(f"\n\u2714 Gespeichert ({len(saved)}/{total}):")
        for s in saved:
            sym_short = s['symbol'].split('/')[0]
            sign = '+' if s['roi_pct'] >= 0 else ''
            lines.append(f"\u2022 {sym_short}/{s['timeframe']}: {sign}{s['roi_pct']}% DD {s['max_dd_pct']}%")
    if failed:
        lines.append(f"\n\u274c Fehlgeschlagen ({len(failed)}/{total}):")
        for fi in failed:
            sym_short = fi['symbol'].split('/')[0]
            lines.append(f"\u2022 {sym_short}/{fi['timeframe']}: {fi['reason']}")
    _send_telegram('\n'.join(lines))


def _init_results_file(start_time: datetime):
    os.makedirs(os.path.dirname(OPTIMIZER_RESULTS_FILE), exist_ok=True)
    with open(OPTIMIZER_RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'run_start': start_time.isoformat(timespec='seconds'),
            'run_end': None,
            'saved': [],
            'failed': [],
        }, f, indent=2)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(pairs: list, opt_settings: dict) -> int:
    python_exe   = sys.executable
    end_date     = date.today().strftime('%Y-%m-%d')
    constraints  = opt_settings.get('constraints', {})

    any_failed = False
    for sym, tf in pairs:
        lookback_days = LOOKBACK_MAP.get(tf, 365)
        start_date    = (date.today() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        _log(f"PAIR_START sym={sym} tf={tf} start={start_date} end={end_date}")

        cmd = [
            python_exe, OPTIMIZER_SCRIPT,
            '--symbol',       sym,
            '--timeframe',    tf,
            '--start_date',   start_date,
            '--end_date',     end_date,
            '--capital',      str(opt_settings.get('start_capital', 15)),
            '--trials',       str(opt_settings.get('num_trials', 500)),
            '--max_drawdown', str(constraints.get('max_drawdown_pct', 30)),
            '--jobs',         str(opt_settings.get('cpu_cores', -1)),
            '--mode',         opt_settings.get('mode', 'best_profit'),
            '--settings',     SETTINGS_FILE,
        ]

        env = os.environ.copy()
        env['PYTHONPATH'] = os.path.join(PROJECT_ROOT, 'src')
        with open(TRIGGER_LOG, 'a', encoding='utf-8') as lf:
            rc = subprocess.run(cmd, stdout=lf, stderr=lf, env=env).returncode
        _log(f"PAIR_EXIT sym={sym} tf={tf} rc={rc}")
        if rc != 0:
            any_failed = True

    return 1 if any_failed else 0


# ---------------------------------------------------------------------------
# Haupt-Ablauf
# ---------------------------------------------------------------------------

def run_optimization(opt_settings: dict, live_settings: dict, reason: str):
    os.makedirs(CACHE_DIR, exist_ok=True)

    pairs        = _resolve_pairs(opt_settings, live_settings)
    pair_display = [f"{sym.split('/')[0]}/{tf}" for sym, tf in pairs]
    num_trials   = int(opt_settings.get('num_trials', 500))
    start_time   = datetime.now()

    _log(f"START reason={reason} pairs={pair_display} trials={num_trials}")

    with open(IN_PROGRESS_FILE, 'w') as f:
        f.write(start_time.isoformat())

    if opt_settings.get('send_telegram_on_completion', False):
        _send_start_telegram(pair_display, num_trials, start_time)

    _init_results_file(start_time)
    start_perf = time.time()
    success    = False

    try:
        rc      = _run_pipeline(pairs, opt_settings)
        success = (rc == 0)
    except Exception as e:
        _log(f"ERROR {e}")
    finally:
        if os.path.exists(IN_PROGRESS_FILE):
            os.remove(IN_PROGRESS_FILE)

    elapsed = round(time.time() - start_perf, 1)

    if success:
        _set_last_run()
        _log(f"FINISH result=success elapsed_s={elapsed}")
        if opt_settings.get('send_telegram_on_completion', False):
            _send_end_telegram(elapsed)
    else:
        _log(f"FINISH result=failed elapsed_s={elapsed}")


def main():
    parser = argparse.ArgumentParser(description='gbot Auto-Optimizer Scheduler')
    parser.add_argument('--force', action='store_true',
                        help='Optimierung sofort erzwingen (ignoriert Zeitplan)')
    args = parser.parse_args()

    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
    except Exception as e:
        print(f"Fehler beim Lesen der settings.json: {e}")
        return

    opt_settings  = settings.get('optimization_settings', {})
    live_settings = settings.get('live_trading_settings', {})

    if not opt_settings.get('enabled', False) and not args.force:
        _log("SKIP optimization disabled in settings.json")
        return

    schedule = opt_settings.get('schedule', {
        'day_of_week': 6, 'hour': 2, 'minute': 0,
        'interval': {'value': 7, 'unit': 'days'},
    })

    if args.force:
        reason = 'forced'
    else:
        due, reason = _is_due(schedule)
        if not due:
            _log("SKIP not due yet")
            return

    run_optimization(opt_settings, live_settings, reason)


if __name__ == '__main__':
    main()
