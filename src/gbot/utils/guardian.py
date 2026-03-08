# src/gbot/utils/guardian.py
import logging
from functools import wraps
from gbot.utils.telegram import send_message


def guardian_decorator(func):
    """
    Decorator, der unerwartete Ausnahmen abfaengt, logged und per Telegram meldet.
    Wirft den Fehler weiter, damit master_runner den Exit-Code sieht.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger = None
        telegram_config = {}
        params = {}

        for arg in args:
            if isinstance(arg, logging.Logger):
                logger = arg
            if isinstance(arg, dict) and 'bot_token' in arg:
                telegram_config = arg
            if isinstance(arg, dict) and 'market' in arg:
                params = arg

        if not logger:
            logger = logging.getLogger("guardian_fallback")
            if not logger.handlers:
                logger.addHandler(logging.StreamHandler())
            logger.setLevel(logging.ERROR)

        try:
            return func(*args, **kwargs)

        except Exception as e:
            symbol = params.get('market', {}).get('symbol', 'Unbekannt')

            logger.critical("=" * 50)
            logger.critical("!!! KRITISCHER FEHLER IM GUARDIAN !!!")
            logger.critical(f"!!! Symbol: {symbol}")
            logger.critical(f"!!! Fehler: {e}", exc_info=True)
            logger.critical("=" * 50)

            try:
                msg = (
                    f"Kritischer Fehler im gbot fuer {symbol}:\n\n"
                    f"{e.__class__.__name__}: {e}\n\n"
                    f"Prozess wird neu gestartet."
                )
                send_message(
                    telegram_config.get('bot_token'),
                    telegram_config.get('chat_id'),
                    msg,
                )
            except Exception as tel_e:
                logger.error(f"Telegram-Fehlernachricht konnte nicht gesendet werden: {tel_e}")

            raise e

    return wrapper
