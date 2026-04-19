# tests/test_workflow.py
# =============================================================================
# gbot: Live-Workflow-Test auf Bitget (PEPE, echte Grid-Limit-Orders)
# =============================================================================
import pytest
import os
import sys
import json
import logging
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from gbot.utils.exchange import Exchange
from gbot.utils.telegram import send_message
from gbot.strategy.grid_logic import (
    calculate_grid_levels,
    split_levels_by_price,
    calculate_amount_per_grid,
)

SYMBOL   = 'PEPE/USDT:USDT'
LEVERAGE = 5
CAPITAL  = 6.0   # USDT — knapp über Bitget-Minimum (5 USDT)
NUM_GRIDS = 2


@pytest.fixture(scope='module')
def test_setup():
    print(f"\n--- Starte gbot Live-Workflow-Test ({SYMBOL}) ---")

    secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
    if not os.path.exists(secret_path):
        pytest.skip("secret.json nicht gefunden.")

    with open(secret_path, 'r') as f:
        secrets = json.load(f)

    gbot_cfg = secrets.get('gbot')
    if not gbot_cfg:
        pytest.skip("Kein 'gbot'-Key in secret.json.")

    account = gbot_cfg if isinstance(gbot_cfg, dict) else gbot_cfg[0]
    telegram_config = secrets.get('telegram', {})

    try:
        exchange = Exchange(account)
        assert exchange.markets, "Exchange: Märkte nicht geladen."
    except Exception as e:
        pytest.fail(f"Exchange-Init fehlgeschlagen: {e}")

    logger = logging.getLogger('gbot-test')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler(sys.stdout))

    print(f"[Setup] Räume offene {SYMBOL}-Orders auf...")
    try:
        cancelled = exchange.cancel_all_orders(SYMBOL)
        print(f"  → {cancelled} offene Orders storniert.")
        time.sleep(2)
    except Exception as e:
        print(f"  Warnung beim Aufräumen: {e}")

    yield exchange, telegram_config, logger

    print(f"\n[Teardown] Storniere alle {SYMBOL}-Orders...")
    try:
        cancelled = exchange.cancel_all_orders(SYMBOL)
        print(f"  → {cancelled} Orders storniert.")
    except Exception as e:
        print(f"  Warnung beim Teardown: {e}")


def test_gbot_grid_workflow_on_bitget(test_setup):
    """
    Live-Workflow-Test fuer gbot:
      1. Balance pruefen
      2. Aktuellen PEPE-Preis holen
      3. Grid-Levels berechnen (2 Grids, manueller Bereich ±2%)
      4. Limit-Buy-Orders unterhalb des Preises platzieren
      5. Orders auf Bitget verifizieren
      6. Alle Orders sauber stornieren
    """
    exchange, telegram_config, logger = test_setup

    # --- 1. Balance ---
    bal = exchange.get_usdt_balance()
    print(f"\n[1/5] Verfuegbares Guthaben: {bal:.4f} USDT")
    if bal < 5.0:
        pytest.skip(f"Nicht genug Guthaben: {bal:.2f} USDT (mind. 5 USDT benoetigt)")

    # --- 2. Aktueller Preis ---
    price = exchange.get_current_price(SYMBOL)
    print(f"[2/5] Aktueller {SYMBOL} Preis: {price:.8f} USDT")
    assert price > 0, "Preis muss positiv sein"

    # --- 3. Grid-Levels berechnen ---
    lower = round(price * 0.98, 8)
    upper = round(price * 1.02, 8)
    levels = calculate_grid_levels(lower, upper, NUM_GRIDS)
    buy_levels, sell_levels = split_levels_by_price(levels, price, mode='neutral')
    amount = calculate_amount_per_grid(CAPITAL, NUM_GRIDS, price, LEVERAGE)

    print(f"[3/5] Grid: lower={lower:.8f}  upper={upper:.8f}  Levels={[f'{l:.8f}' for l in levels]}")
    print(f"      Buy-Levels: {[f'{l:.8f}' for l in buy_levels]}")
    print(f"      Menge/Grid: {amount:.2f} PEPE  (Kapital={CAPITAL} USDT, {LEVERAGE}x)")

    assert len(buy_levels) > 0, "Mindestens ein Buy-Level erwartet"

    # --- 4. Leverage + Margin setzen, dann Orders platzieren ---
    print(f"[4/5] Setze Leverage={LEVERAGE}x, Margin=isolated...")
    exchange.set_leverage(SYMBOL, LEVERAGE, margin_mode='isolated')
    time.sleep(1)

    min_amount = exchange.get_min_order_amount(SYMBOL)
    order_amount = max(amount, min_amount * 1.05)
    order_amount = exchange.round_amount(SYMBOL, order_amount)

    placed_ids = []
    for bp in buy_levels:
        bp_rounded = exchange.round_price(SYMBOL, bp)
        notional = bp_rounded * order_amount
        if notional < 5.0:
            print(f"  Ueberspringe Level {bp_rounded:.8f} — Notional {notional:.4f} USDT < 5 USDT")
            continue
        try:
            order = exchange.place_limit_order(SYMBOL, 'buy', order_amount, bp_rounded)
            assert order and order.get('id'), f"Order-Platzierung fehlgeschlagen fuer Level {bp_rounded}"
            placed_ids.append(order['id'])
            print(f"  ✔ Buy-Order platziert: {order_amount:.2f} PEPE @ {bp_rounded:.8f}  (ID: {order['id']})")
        except Exception as e:
            pytest.fail(f"Fehler beim Platzieren der Order @ {bp_rounded}: {e}")

    assert len(placed_ids) > 0, "Keine einzige Order konnte platziert werden"

    # --- Telegram-Benachrichtigung ---
    bot_token = telegram_config.get('bot_token')
    chat_id   = telegram_config.get('chat_id')
    order_lines = '\n'.join(
        f'  Buy {order_amount:.0f} PEPE @ {exchange.round_price(SYMBOL, bp):.8f}'
        for bp in buy_levels
        if exchange.round_price(SYMBOL, bp) * order_amount >= 5.0
    )
    msg = (
        f'GBOT TEST: {SYMBOL}\n'
        f'Leverage: {LEVERAGE}x isolated\n'
        f'Kapital: {CAPITAL} USDT\n'
        f'Preis: {price:.8f}\n'
        f'Grid: {lower:.8f} - {upper:.8f}\n'
        f'{order_lines}\n'
        f'{len(placed_ids)} Order(s) platziert - werden in 10s storniert.'
    )
    send_message(bot_token, chat_id, msg)
    print('  Telegram-Benachrichtigung gesendet. Warte 10s...')
    time.sleep(10)

    # --- 5. Orders auf Bitget verifizieren ---
    print(f"[5/5] Verifiziere {len(placed_ids)} Order(s) auf Bitget...")
    open_orders = exchange.fetch_open_orders(SYMBOL)
    open_ids = {o['id'] for o in open_orders}

    for oid in placed_ids:
        assert oid in open_ids, f"Order {oid} nicht in offenen Orders gefunden!"
    print(f"  ✔ Alle {len(placed_ids)} Orders bestätigt auf Bitget.")

    # --- 6. Aufräumen ---
    print("  Storniere Test-Orders...")
    cancelled = exchange.cancel_all_orders(SYMBOL)
    time.sleep(2)
    remaining = exchange.fetch_open_orders(SYMBOL)
    assert len(remaining) == 0, f"Nach Stornierung noch {len(remaining)} offene Orders!"
    print(f"  ✔ Alle Orders sauber storniert.")

    print("\n--- GBOT WORKFLOW-TEST ERFOLGREICH! ---")
