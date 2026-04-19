# src/gbot/utils/exchange.py
import ccxt
import pandas as pd
from datetime import datetime, timezone
import logging
import time
from typing import Optional, Dict, List, Any
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

logger = logging.getLogger(__name__)


class Exchange:
    def __init__(self, account_config: dict):
        self.account = account_config
        self.exchange = getattr(ccxt, 'bitget')({
            'apiKey': self.account.get('apiKey'),
            'secret': self.account.get('secret'),
            'password': self.account.get('password'),
            'options': {
                'defaultType': 'swap',
            },
            'enableRateLimit': True,
        })

        try:
            self.markets = self.exchange.load_markets()
            logger.info("Maerkte erfolgreich geladen.")
        except Exception as e:
            logger.critical(f"Konnte Maerkte nicht laden: {e}")
            self.markets = {}

    # --- Markt-Infos ---
    def fetch_ticker(self, symbol: str) -> dict:
        """Aktuellen Ticker (Preis etc.) abrufen."""
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des Tickers fuer {symbol}: {e}")
            raise

    def get_current_price(self, symbol: str) -> float:
        """Aktuellen Mid-Preis abrufen."""
        ticker = self.fetch_ticker(symbol)
        price = ticker.get('last') or ticker.get('close')
        if price is None:
            raise ValueError(f"Kein Preis im Ticker fuer {symbol} gefunden.")
        return float(price)

    def fetch_balance(self) -> dict:
        """Kontostand abrufen."""
        try:
            return self.exchange.fetch_balance({'type': 'swap'})
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des Kontostands: {e}")
            raise

    def get_usdt_balance(self) -> float:
        """Verfuegbares USDT-Guthaben."""
        balance = self.fetch_balance()
        usdt = balance.get('USDT', {}).get('free', 0.0)
        return float(usdt)

    # --- Hebel & Margin ---
    def set_margin_mode(self, symbol: str, margin_mode: str = 'isolated'):
        margin_mode_lower = margin_mode.lower()
        try:
            params = {'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'}
            self.exchange.set_margin_mode(margin_mode_lower, symbol, params=params)
            logger.info(f"Margin-Modus fuer {symbol} auf '{margin_mode_lower}' gesetzt.")
        except ccxt.ExchangeError as e:
            if 'Margin mode is the same' in str(e) or 'margin mode is not changed' in str(e).lower() or '40051' in str(e):
                logger.info(f"Margin-Modus fuer {symbol} bereits '{margin_mode_lower}' (unveraendert).")
            elif '40014' in str(e):
                logger.warning(f"Margin-Modus konnte nicht gesetzt werden (API-Key Permission fehlt) — wird uebersprungen.")
            else:
                logger.error(f"Fehler beim Setzen des Margin-Modus fuer {symbol}: {e}")
        except Exception as e:
            logger.error(f"Unerwarteter Fehler beim Setzen des Margin-Modus fuer {symbol}: {e}")

    def set_leverage(self, symbol: str, leverage: int, margin_mode: str = 'isolated'):
        try:
            leverage = int(leverage)
            params = {'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'}
            if margin_mode.lower() == 'isolated':
                self.exchange.set_leverage(leverage, symbol, params={**params, 'holdSide': 'long'})
                import time as _time; _time.sleep(0.2)
                self.exchange.set_leverage(leverage, symbol, params={**params, 'holdSide': 'short'})
            else:
                self.exchange.set_leverage(leverage, symbol, params=params)
            logger.info(f"Hebel {leverage}x ({margin_mode}) fuer {symbol} gesetzt.")
        except ccxt.ExchangeError as e:
            if 'Leverage not changed' in str(e) or 'leverage is not modified' in str(e).lower() or '40052' in str(e):
                logger.info(f"Hebel fuer {symbol} bereits {leverage}x (unveraendert).")
            elif '40014' in str(e):
                logger.warning(f"Hebel konnte nicht gesetzt werden (API-Key Permission fehlt) — wird uebersprungen.")
            else:
                logger.error(f"Fehler beim Setzen des Hebels fuer {symbol}: {e}")
        except Exception as e:
            logger.error(f"Unerwarteter Fehler beim Setzen des Hebels fuer {symbol}: {e}")

    # --- Auftraege ---
    def place_limit_order(self, symbol: str, side: str, amount: float, price: float, params: dict = None, margin_mode: str = 'isolated') -> dict:
        """
        Limit-Order platzieren.
        side: 'buy' (Long eroeffnen) oder 'sell' (Long schliessen / Short eroeffnen)
        amount: Menge in Kontrakten/Coin-Einheiten
        price: Limit-Preis
        """
        if params is None:
            params = {}
        try:
            order = self.exchange.create_order(symbol, 'limit', side, amount, price, params)
            logger.info(f"Limit-Order platziert: {side.upper()} {amount} {symbol} @ {price} | ID: {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Order ({side} {amount} {symbol} @ {price}): {e}")
            raise

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Order stornieren."""
        try:
            result = self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Order {order_id} storniert.")
            return result
        except ccxt.OrderNotFound:
            logger.warning(f"Order {order_id} nicht gefunden (evtl. bereits ausgefuehrt oder storniert).")
            return {}
        except Exception as e:
            logger.error(f"Fehler beim Stornieren der Order {order_id}: {e}")
            raise

    def fetch_open_orders(self, symbol: str) -> List[dict]:
        """Alle offenen Orders fuer ein Symbol abrufen."""
        try:
            orders = self.exchange.fetch_open_orders(symbol, params={'productType': 'USDT-FUTURES'})
            return orders
        except Exception as e:
            logger.error(f"Fehler beim Abrufen offener Orders fuer {symbol}: {e}")
            raise

    def fetch_order(self, order_id: str, symbol: str) -> dict:
        """Spezifische Order abrufen."""
        try:
            return self.exchange.fetch_order(order_id, symbol)
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der Order {order_id}: {e}")
            raise

    def cancel_all_orders(self, symbol: str) -> int:
        """Alle offenen Orders fuer ein Symbol stornieren. Gibt Anzahl zurueck."""
        open_orders = self.fetch_open_orders(symbol)
        count = 0
        for order in open_orders:
            try:
                self.cancel_order(order['id'], symbol)
                count += 1
                time.sleep(0.2)
            except Exception as e:
                logger.warning(f"Konnte Order {order['id']} nicht stornieren: {e}")
        logger.info(f"{count} Orders fuer {symbol} storniert.")
        return count

    def get_market_precision(self, symbol: str) -> dict:
        """Praezision (Nachkommastellen) fuer Preis und Menge abrufen."""
        if symbol not in self.markets:
            raise ValueError(f"Symbol {symbol} nicht im Markt gefunden.")
        market = self.markets[symbol]
        return {
            'price': market.get('precision', {}).get('price', 8),
            'amount': market.get('precision', {}).get('amount', 8),
        }

    def round_price(self, symbol: str, price: float) -> float:
        """Preis auf Markt-Praezision runden."""
        try:
            return float(self.exchange.price_to_precision(symbol, price))
        except Exception:
            return round(price, 2)

    def round_amount(self, symbol: str, amount: float) -> float:
        """Menge auf Markt-Praezision runden."""
        try:
            return float(self.exchange.amount_to_precision(symbol, amount))
        except Exception:
            return round(amount, 6)

    def fetch_open_positions(self, symbol: str) -> list:
        """Offene Positionen für ein Symbol abrufen."""
        try:
            params = {'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'}
            positions = self.exchange.fetch_positions([symbol], params=params)
            return [p for p in positions if float(p.get('contracts', 0) or 0) != 0]
        except Exception as e:
            logger.error(f"Fehler beim Abrufen offener Positionen fuer {symbol}: {e}")
            return []

    def close_all_positions(self, symbol: str) -> bool:
        """Schliesst alle offenen Positionen fuer ein Symbol mit Market-Order (reduceOnly)."""
        positions = self.fetch_open_positions(symbol)
        if not positions:
            logger.info(f"Keine offenen Positionen fuer {symbol}.")
            return True
        success = True
        for pos in positions:
            contracts = float(pos.get('contracts', 0) or 0)
            side = pos.get('side', '').lower()
            if contracts == 0:
                continue
            close_side = 'sell' if side == 'long' else 'buy'
            try:
                self.exchange.create_order(symbol, 'market', close_side, contracts, None, {'reduceOnly': True})
                logger.info(f"Position geschlossen: {close_side.upper()} {contracts} {symbol}")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Position schliessen fehlgeschlagen ({close_side} {contracts} {symbol}): {e}")
                success = False
        return success

    def get_min_order_amount(self, symbol: str) -> float:
        """Mindestbestellmenge abrufen."""
        if symbol not in self.markets:
            return 0.0
        market = self.markets[symbol]
        return float(market.get('limits', {}).get('amount', {}).get('min', 0.0) or 0.0)

    # --- OHLCV ---
    def fetch_recent_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        """Aktuelle OHLCV-Daten abrufen."""
        if not self.markets:
            return pd.DataFrame()

        timeframe_ms = self.exchange.parse_timeframe(timeframe) * 1000
        since = self.exchange.milliseconds() - timeframe_ms * limit
        all_ohlcv = []
        fetch_limit = 200

        while since < self.exchange.milliseconds():
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since, fetch_limit)
                if not ohlcv:
                    break
                all_ohlcv.extend(ohlcv)
                since = ohlcv[-1][0] + timeframe_ms
                time.sleep(self.exchange.rateLimit / 1000)
            except ccxt.RateLimitExceeded:
                logger.warning("Rate limit - warte 5s...")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Fehler beim Abrufen von OHLCV: {e}")
                break

        if not all_ohlcv:
            return pd.DataFrame()

        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        df = df[~df.index.duplicated(keep='last')]
        return df.iloc[-limit:]
