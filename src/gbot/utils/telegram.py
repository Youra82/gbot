# src/gbot/utils/telegram.py
import requests
import logging
import os

logger = logging.getLogger(__name__)


def send_message(bot_token: str, chat_id: str, message: str):
    """Sendet eine Textnachricht via Telegram (MarkdownV2)."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID fehlt. Nachricht nicht gesendet.")
        return

    escape_chars = r'_*[]()~`>#+-=|{}.!'
    escaped = message
    for char in escape_chars:
        escaped = escaped.replace(char, f'\\{char}')

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': escaped, 'parse_mode': 'MarkdownV2'}

    try:
        response = requests.post(api_url, data=payload, timeout=10)
        response.raise_for_status()
        logger.debug(f"Telegram-Nachricht gesendet.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Senden der Telegram-Nachricht: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler beim Senden der Telegram-Nachricht: {e}")


def send_document(bot_token: str, chat_id: str, file_path: str, caption: str = ''):
    """Sendet eine Datei (z.B. HTML-Chart) via Telegram."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID fehlt. Dokument nicht gesendet.")
        return
    if not os.path.exists(file_path):
        logger.error(f"Datei nicht gefunden: {file_path}")
        return

    api_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        with open(file_path, 'rb') as f:
            response = requests.post(
                api_url,
                data={'chat_id': chat_id, 'caption': caption[:1024]},
                files={'document': (os.path.basename(file_path), f)},
                timeout=60,
            )
        response.raise_for_status()
        logger.info(f"Dokument gesendet: {os.path.basename(file_path)}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Senden des Dokuments: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler beim Senden des Dokuments: {e}")
