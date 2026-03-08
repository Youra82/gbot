#!/usr/bin/env python3
"""
Interactive Charts fuer gbot.
Zeigt Candlestick-Chart mit Fibonacci-Zonen + Grid-Levels + PnL-Kurve.
Speichert als HTML, optional Versand via Telegram.
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

logger = logging.getLogger('interactive_charts')
if not logger.handlers:
    logger.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logger.addHandler(ch)

CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'gbot', 'strategy', 'configs')


# ---------------------------------------------------------------------------
# Config-Auswahl
# ---------------------------------------------------------------------------

def get_config_files():
    import glob
    files = sorted(glob.glob(os.path.join(CONFIGS_DIR, 'config_*.json')))
    return [(os.path.basename(f), f) for f in files]


def select_configs():
    configs = get_config_files()
    if not configs:
        logger.error("Keine Konfigurationsdateien gefunden!")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Verfuegbare Konfigurationen:")
    print("=" * 60)
    for idx, (filename, _) in enumerate(configs, 1):
        clean = filename.replace('config_', '').replace('.json', '')
        print(f"{idx:2d}) {clean}")
    print("=" * 60)
    print("\nWaehle Konfiguration(en) zum Anzeigen:")
    print("  Einzeln:  z.B. '1' oder '3'")
    print("  Mehrfach: z.B. '1,3,5' oder '1 3 5'")

    selection = input("\nAuswahl: ").strip()
    selected = []
    for part in selection.replace(',', ' ').split():
        try:
            idx = int(part)
            if 1 <= idx <= len(configs):
                selected.append(configs[idx - 1])
            else:
                logger.warning(f"Index {idx} ausserhalb des Bereichs")
        except ValueError:
            logger.warning(f"Ungueltige Eingabe: {part}")

    if not selected:
        logger.error("Keine gueltigen Konfigurationen gewaehlt!")
        sys.exit(1)

    return selected


# ---------------------------------------------------------------------------
# PnL-Kurve aus Backtest aufbauen
# ---------------------------------------------------------------------------

def build_pnl_curve(df: pd.DataFrame, lower: float, upper: float,
                    num_grids: int, leverage: float, capital: float) -> pd.DataFrame:
    """
    Fuehrt den Grid-Backtest candle-by-candle durch und gibt
    eine DataFrame mit kumulativem PnL zurueck.
    """
    from gbot.analysis.backtester import DEFAULT_FEE_RATE, _r

    if upper <= lower or num_grids < 2:
        return pd.DataFrame()

    spacing = (upper - lower) / num_grids
    levels = [_r(lower + i * spacing) for i in range(num_grids + 1)]
    mid_price = (upper + lower) / 2.0
    amount = (capital * leverage) / (num_grids * mid_price)
    fee_rate = DEFAULT_FEE_RATE

    init_price = float(df['close'].iloc[0])
    buy_orders = {l for l in levels if l < init_price}
    sell_orders = {l for l in levels if l > init_price}

    total_pnl = 0.0
    pnl_series = []

    for ts, row in df.iterrows():
        candle_low = float(row['low'])
        candle_high = float(row['high'])
        new_sell = set()
        new_buy = set()

        for bp in list(buy_orders):
            if candle_low <= bp:
                total_pnl -= bp * amount * fee_rate
                buy_orders.discard(bp)
                sp = _r(bp + spacing)
                if sp <= _r(upper) + 1e-9:
                    new_sell.add(sp)

        for sp in list(sell_orders):
            if candle_high >= sp:
                total_pnl += spacing * amount - sp * amount * fee_rate
                sell_orders.discard(sp)
                bp = _r(sp - spacing)
                if bp >= _r(lower) - 1e-9:
                    new_buy.add(bp)

        sell_orders.update(new_sell - sell_orders)
        buy_orders.update(new_buy - buy_orders)
        pnl_series.append({'timestamp': ts, 'pnl': round(total_pnl, 4),
                           'capital': round(capital + total_pnl, 4)})

    return pd.DataFrame(pnl_series).set_index('timestamp')


# ---------------------------------------------------------------------------
# Plotly-Chart erstellen
# ---------------------------------------------------------------------------

def create_chart(symbol: str, timeframe: str, df: pd.DataFrame,
                 fib_levels: dict, lower: float, upper: float,
                 num_grids: int, leverage: int, capital: float,
                 pnl_df: pd.DataFrame, current_price: float,
                 swing: dict, suggested: dict,
                 start_date=None, end_date=None, window=None):
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        logger.error("plotly nicht installiert. Bitte: pip install plotly")
        return None

    # Zeitraum-Filter
    if window:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window)
        df = df[df.index >= cutoff].copy()
        pnl_df = pnl_df[pnl_df.index >= cutoff] if not pnl_df.empty else pnl_df
    if start_date:
        df = df[df.index >= pd.to_datetime(start_date, utc=True)]
        if not pnl_df.empty:
            pnl_df = pnl_df[pnl_df.index >= pd.to_datetime(start_date, utc=True)]
    if end_date:
        df = df[df.index <= pd.to_datetime(end_date, utc=True)]
        if not pnl_df.empty:
            pnl_df = pnl_df[pnl_df.index <= pd.to_datetime(end_date, utc=True)]

    if df.empty:
        logger.warning(f"Keine Daten im Zeitraum fuer {symbol}")
        return None

    x0 = df.index.min()
    x1 = df.index.max()

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # ===== CANDLESTICKS =====
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'], high=df['high'],
        low=df['low'], close=df['close'],
        name='OHLC',
        increasing_line_color='#16a34a',
        decreasing_line_color='#dc2626',
        showlegend=True,
    ), secondary_y=False)

    # ===== FIBONACCI ZONEN als farbige Baender =====
    fib_colors = {
        '0.0%':   ('rgba(239,68,68,0.08)',  'rgba(239,68,68,0.5)'),
        '23.6%':  ('rgba(249,115,22,0.08)', 'rgba(249,115,22,0.5)'),
        '38.2%':  ('rgba(234,179,8,0.12)',  'rgba(234,179,8,0.6)'),
        '50.0%':  ('rgba(168,85,247,0.10)', 'rgba(168,85,247,0.5)'),
        '61.8%':  ('rgba(234,179,8,0.12)',  'rgba(234,179,8,0.6)'),
        '78.6%':  ('rgba(249,115,22,0.08)', 'rgba(249,115,22,0.5)'),
        '100%':   ('rgba(239,68,68,0.08)',  'rgba(239,68,68,0.5)'),
    }

    sorted_levels = sorted(fib_levels.items(), key=lambda x: x[1])
    for i in range(len(sorted_levels) - 1):
        label_lo, price_lo = sorted_levels[i]
        label_hi, price_hi = sorted_levels[i + 1]
        fill, line_c = fib_colors.get(label_lo, ('rgba(100,100,100,0.05)', 'rgba(100,100,100,0.3)'))
        is_grid_range = (abs(price_lo - lower) < 0.01 and abs(price_hi - upper) < 0.01)
        if is_grid_range:
            fill = 'rgba(37,99,235,0.12)'
            line_c = 'rgba(37,99,235,0.7)'

        fig.add_shape(type='rect', x0=x0, x1=x1, y0=price_lo, y1=price_hi,
                      fillcolor=fill, line=dict(color=line_c, width=1, dash='dot'), layer='below')

        mid = (price_lo + price_hi) / 2
        fig.add_shape(type='line', x0=x0, x1=x1, y0=price_hi, y1=price_hi,
                      line=dict(color=line_c, width=1), layer='below')
        fig.add_annotation(x=x1, y=price_hi, text=f" {label_hi}",
                           showarrow=False, xanchor='left',
                           font=dict(size=10, color=line_c), yref='y')

    # ===== GRID-LEVELS als gestrichelte Linien =====
    spacing = (upper - lower) / num_grids
    for i in range(num_grids + 1):
        gp = lower + i * spacing
        is_boundary = (i == 0 or i == num_grids)
        color = '#1d4ed8' if is_boundary else '#93c5fd'
        width = 1.5 if is_boundary else 0.8
        dash = 'solid' if is_boundary else 'dash'
        fig.add_shape(type='line', x0=x0, x1=x1, y0=gp, y1=gp,
                      line=dict(color=color, width=width, dash=dash), layer='above')
        if is_boundary:
            label = f" Grid {'Unten' if i == 0 else 'Oben'} ({suggested['lower_label'] if i == 0 else suggested['upper_label']})"
            fig.add_annotation(x=x0, y=gp, text=label, showarrow=False,
                               xanchor='right', font=dict(size=10, color='#1d4ed8'), yref='y')

    # ===== AKTUELLER PREIS =====
    fig.add_shape(type='line', x0=x0, x1=x1, y0=current_price, y1=current_price,
                  line=dict(color='#f59e0b', width=2, dash='dot'), layer='above')
    fig.add_annotation(x=x1, y=current_price,
                       text=f" Preis: {current_price:,.4f}",
                       showarrow=False, xanchor='left',
                       font=dict(size=11, color='#f59e0b', weight='bold'), yref='y')

    # ===== PNL / KONTOSTAND (rechte Y-Achse) =====
    if not pnl_df.empty and 'capital' in pnl_df.columns:
        fig.add_trace(go.Scatter(
            x=pnl_df.index, y=pnl_df['capital'],
            name='Kontostand',
            line=dict(color='#2563eb', width=2),
            opacity=0.8,
            showlegend=True,
        ), secondary_y=True)

    # ===== TITEL =====
    total_pnl = pnl_df['pnl'].iloc[-1] if not pnl_df.empty else 0
    roi = total_pnl / capital * 100 if capital > 0 else 0
    title_text = (
        f"{symbol} {timeframe} - gbot Grid | "
        f"Kapital: {capital:.0f} USDT | "
        f"PnL: {'+' if total_pnl >= 0 else ''}{total_pnl:.2f} USDT ({roi:+.1f}%) | "
        f"Grids: {num_grids} | Hebel: {leverage}x | "
        f"Trend: {swing['trend'].upper()}"
    )

    fig.update_layout(
        title=dict(text=title_text, font=dict(size=13), x=0.5, xanchor='center'),
        height=720,
        hovermode='x unified',
        template='plotly_white',
        dragmode='zoom',
        xaxis=dict(rangeslider=dict(visible=True), fixedrange=False),
        yaxis=dict(fixedrange=False),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
        showlegend=True,
    )
    fig.update_yaxes(title_text='Preis (USDT)', secondary_y=False)
    fig.update_yaxes(title_text='Kontostand (USDT)', secondary_y=True)

    return fig


# ---------------------------------------------------------------------------
# Haupt-Funktion
# ---------------------------------------------------------------------------

def main():
    selected_configs = select_configs()

    print("\n" + "=" * 60)
    print("Chart-Optionen:")
    print("=" * 60)
    start_date = input("Startdatum (YYYY-MM-DD) [leer=auto]:      ").strip() or None
    end_date   = input("Enddatum   (YYYY-MM-DD) [leer=heute]:     ").strip() or None
    cap_input  = input("Gesamtkapital (USDT)    [Standard: 100]:  ").strip()
    capital    = float(cap_input) if cap_input else 100.0
    win_input  = input("Letzten N Tage anzeigen [leer=alle]:      ").strip()
    window     = int(win_input) if win_input.isdigit() else None
    tg_input   = input("Telegram versenden? (j/n) [Standard: n]:  ").strip().lower()
    send_telegram = tg_input in ['j', 'y', 'yes']

    try:
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
            secrets = json.load(f)
        telegram_config = secrets.get('telegram', {})
    except Exception:
        telegram_config = {}

    from gbot.analysis.fibonacci import fetch_ohlcv_public, auto_fib_analysis
    from gbot.analysis.optimizer import LOOKBACK_BY_TF, DEFAULT_LOOKBACK

    for filename, filepath in selected_configs:
        try:
            with open(filepath, 'r') as f:
                cfg = json.load(f)

            symbol = cfg['market']['symbol']
            fib_cfg = cfg['grid'].get('fibonacci', {})
            tf = fib_cfg.get('timeframe', '4h')
            lookback = LOOKBACK_BY_TF.get(tf, DEFAULT_LOOKBACK)
            num_grids = cfg['grid']['num_grids']
            leverage = cfg['risk'].get('leverage', 1)

            logger.info(f"\nVerarbeite {symbol} ({tf})...")

            logger.info("Lade OHLCV-Daten...")
            df = fetch_ohlcv_public(symbol, tf, lookback)
            if start_date:
                df = df[df.index >= start_date]
            if end_date:
                df = df[df.index <= end_date]

            if df.empty:
                logger.warning(f"Keine Daten fuer {symbol}")
                continue

            logger.info("Berechne Fibonacci-Range...")
            analysis = auto_fib_analysis(
                symbol=symbol,
                timeframe=tf,
                lookback=fib_cfg.get('lookback', 200),
                swing_window=fib_cfg.get('swing_window', 10),
                prefer_golden_zone=fib_cfg.get('prefer_golden_zone', False),
            )
            fib_levels = analysis['fib_levels']
            suggested  = analysis['suggested_range']
            swing      = analysis['swing_points']
            current    = analysis['current_price']
            lower      = suggested['lower_price']
            upper      = suggested['upper_price']

            logger.info("Berechne PnL-Kurve...")
            pnl_df = build_pnl_curve(df, lower, upper, num_grids, leverage, capital)

            logger.info("Erstelle interaktiven Chart...")
            fig = create_chart(
                symbol=symbol, timeframe=tf, df=df,
                fib_levels=fib_levels, lower=lower, upper=upper,
                num_grids=num_grids, leverage=leverage, capital=capital,
                pnl_df=pnl_df, current_price=current,
                swing=swing, suggested=suggested,
                start_date=start_date, end_date=end_date, window=window,
            )

            if fig is None:
                continue

            safe = symbol.replace('/', '_').replace(':', '_')
            output_file = f"/tmp/gbot_{safe}_{tf}.html"
            fig.write_html(output_file)
            logger.info(f"Chart gespeichert: {output_file}")

            if send_telegram and telegram_config.get('bot_token'):
                try:
                    from gbot.utils.telegram import send_document
                    send_document(
                        telegram_config['bot_token'],
                        telegram_config['chat_id'],
                        output_file,
                        caption=f"gbot Grid Chart: {symbol} {tf}",
                    )
                    logger.info("Chart via Telegram versendet")
                except Exception as e:
                    logger.warning(f"Telegram-Versand fehlgeschlagen: {e}")

        except Exception as e:
            logger.error(f"Fehler bei {filename}: {e}", exc_info=True)
            continue

    logger.info("\nAlle Charts generiert!")


if __name__ == '__main__':
    main()
