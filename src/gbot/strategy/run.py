# src/gbot/strategy/run.py
"""
Einstiegspunkt fuer eine einzelne Grid-Strategie-Instanz.
Wird vom master_runner.py per Cronjob aufgerufen.

Aufruf:
  python3 src/gbot/strategy/run.py --symbol BTC/USDT:USDT
"""
import os
import sys
import json
import logging
import argparse
from logging.handlers import RotatingFileHandler

import ccxt

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from gbot.utils.exchange import Exchange
from gbot.utils.telegram import send_message
from gbot.utils.guardian import guardian_decorator
from gbot.utils.trade_manager import full_grid_cycle


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(symbol: str) -> logging.Logger:
    safe = symbol.replace('/', '').replace(':', '').replace('-', '')
    log_dir = os.path.join(PROJECT_ROOT, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'gbot_{safe}.log')
    logger_name = f'gbot_{safe}'
    logger = logging.getLogger(logger_name)

    if not logger.handlers:
        logger.setLevel(logging.INFO)

        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(f'%(asctime)s [{safe}] %(levelname)s: %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(ch)

        logger.propagate = False

    return logger


# ---------------------------------------------------------------------------
# Konfiguration laden
# ---------------------------------------------------------------------------

def load_config(symbol: str) -> dict:
    """
    Laedt die JSON-Konfiguration fuer das angegebene Symbol.
    Dateiname: config_<SYMBOLCLEAN>.json
    """
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'gbot', 'strategy', 'configs')
    safe = symbol.replace('/', '').replace(':', '').replace('-', '_')
    filename = f"config_{safe}.json"
    path = os.path.join(configs_dir, filename)

    if not os.path.exists(path):
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {path}")

    with open(path, 'r') as f:
        config = json.load(f)

    required = {'market', 'grid', 'risk'}
    missing = required - set(config.keys())
    if missing:
        raise ValueError(f"Konfiguration unvollstaendig, fehlende Abschnitte: {missing}")

    return config


# ---------------------------------------------------------------------------
# Haupt-Bot-Funktion
# ---------------------------------------------------------------------------

@guardian_decorator
def run_for_account(account: dict, telegram_config: dict, params: dict, logger: logging.Logger):
    """Fuehrt einen Grid-Zyklus fuer einen Account aus."""
    account_name = account.get('name', 'Standard-Account')
    symbol = params['market']['symbol']

    logger.info(f"--- gbot Grid-Zyklus fuer {symbol} auf Account '{account_name}' ---")

    try:
        exchange = Exchange(account)
        full_grid_cycle(exchange, params, telegram_config, logger)

    except ccxt.AuthenticationError:
        logger.critical("Authentifizierungsfehler! API-Keys pruefen.")
        raise
    except ccxt.NotSupported as e:
        logger.critical(f"Funktion nicht unterstuetzt: {e}")
        raise
    except Exception as e:
        logger.error(f"Fehler im Grid-Zyklus fuer {symbol}: {e}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="gbot Grid-Trading-Skript")
    parser.add_argument('--symbol', required=True, type=str, help="Handelspaar (z.B. BTC/USDT:USDT)")
    args = parser.parse_args()

    symbol = args.symbol
    logger = setup_logging(symbol)

    try:
        params = load_config(symbol)
        logger.info(f"Konfiguration geladen fuer {symbol}.")

        with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
            secrets = json.load(f)

        accounts = secrets.get('gbot', [])
        telegram_config = secrets.get('telegram', {})

        if not accounts:
            logger.critical("Keine 'gbot'-Accounts in secret.json gefunden.")
            sys.exit(1)

    except FileNotFoundError as e:
        logger.critical(f"Datei nicht gefunden: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.critical(f"Konfigurationsfehler: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Initialisierungsfehler: {e}", exc_info=True)
        sys.exit(1)

    for account in accounts:
        try:
            run_for_account(account, telegram_config, params, logger)
        except Exception as e:
            logger.error(f"Schwerwiegender Fehler fuer Account {account.get('name', 'Unbenannt')}: {e}")
            sys.exit(1)

    logger.info(f">>> gbot-Lauf fuer {symbol} abgeschlossen <<<")


if __name__ == "__main__":
    main()
