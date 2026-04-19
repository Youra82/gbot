# master_runner.py
"""
gbot Master Runner.

Liest settings.json, startet fuer jede aktive Grid-Strategie einen
separaten run.py Prozess. Wird per Cronjob regelmaessig aufgerufen.

Cronjob-Beispiel (alle 5 Minuten):
  */5 * * * * cd /pfad/zu/gbot && .venv/bin/python3 master_runner.py
"""
import json
import os
import subprocess
import sys
import time
import logging

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

log_dir = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'master_runner.log')),
        logging.StreamHandler(),
    ],
)


def main():
    settings_file = os.path.join(SCRIPT_DIR, 'settings.json')
    secret_file = os.path.join(SCRIPT_DIR, 'secret.json')
    bot_script = os.path.join(SCRIPT_DIR, 'src', 'gbot', 'strategy', 'run.py')
    python = os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3')

    if not os.path.exists(python):
        logging.critical(f"Python-Interpreter nicht gefunden: {python}")
        return

    logging.info("=" * 55)
    logging.info("gbot Master Runner")
    logging.info("=" * 55)

    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)

        with open(secret_file, 'r') as f:
            secrets = json.load(f)

        if not secrets.get('gbot'):
            logging.critical("Kein 'gbot'-Account in secret.json gefunden.")
            return

        live_settings = settings.get('live_trading_settings', {})
        active_strategies = live_settings.get('active_strategies', [])

        if not active_strategies:
            logging.warning("Keine aktiven Strategien in settings.json gefunden.")
            return

        logging.info(f"Gefundene Strategien: {len(active_strategies)}")

        for strategy in active_strategies:
            if not isinstance(strategy, dict):
                logging.warning(f"Ungueltige Strategie-Konfiguration: {strategy}")
                continue

            if not strategy.get('active', False):
                continue

            symbol = strategy.get('symbol')
            if not symbol:
                logging.warning(f"Keine 'symbol' in Strategie-Konfiguration: {strategy}")
                continue

            timeframe = strategy.get('timeframe')
            label = f"{symbol} ({timeframe})" if timeframe else symbol
            logging.info(f"Starte Grid-Bot fuer: {label}")

            command = [python, bot_script, '--symbol', symbol]
            if timeframe:
                command += ['--timeframe', timeframe]

            try:
                process = subprocess.Popen(command)
                logging.info(f"Prozess gestartet (PID: {process.pid}) fuer {symbol}.")
                time.sleep(2)
            except Exception as e:
                logging.error(f"Fehler beim Starten des Prozesses fuer {symbol}: {e}")

    except FileNotFoundError as e:
        logging.critical(f"Datei nicht gefunden: {e}")
    except json.JSONDecodeError as e:
        logging.critical(f"JSON-Fehler: {e}")
    except Exception as e:
        logging.critical(f"Unerwarteter Fehler: {e}", exc_info=True)


if __name__ == "__main__":
    main()
