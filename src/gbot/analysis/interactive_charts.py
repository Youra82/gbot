#!/usr/bin/env python3
"""
Interactive Charts fuer gbot.
Zeigt Candlestick-Chart mit dynamischen Fibonacci-Zonen + Grid-Levels + PnL-Kurve.

Fibonacci wird dynamisch berechnet — genau wie im Live-Bot:
  - Initialisierung: erste lookback_fib Kerzen bestimmen den Grid-Bereich
  - Rebalancing: wenn Preis die Range verlaesst (Cooldown beachten),
    wird Fibonacci neu berechnet und das Grid neu gesetzt
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
# Dynamisches Fibonacci-Rebalancing (wie Live-Bot)
# ---------------------------------------------------------------------------

def simulate_dynamic_grid(df: pd.DataFrame, num_grids: int, leverage: float,
                           capital: float, lookback_fib: int = 200,
                           swing_window: int = 10, prefer_golden_zone: bool = False,
                           min_rebalance_hours: int = 4) -> tuple:
    """
    Simuliert das Grid mit dynamischem Fibonacci-Rebalancing — exakt wie der Live-Bot.

    - Fibonacci wird initial aus den ersten lookback_fib Kerzen berechnet
    - Wenn Preis die Range verlaesst (nach Cooldown): Fibonacci neu berechnen
    - Jede Grid-Epoche wird aufgezeichnet: (start_ts, end_ts, lower, upper, fib_levels)

    Returns:
        (grid_epochs, pnl_df)
        grid_epochs: Liste von Dicts {start_ts, end_ts, lower, upper, lower_label,
                                      upper_label, fib_levels, swing}
        pnl_df: DataFrame mit kumulativem PnL-Verlauf
    """
    from gbot.analysis.fibonacci import find_swing_high_low, calculate_fib_levels, find_best_grid_range
    from gbot.analysis.backtester import DEFAULT_FEE_RATE, _r

    min_rebalance_td = pd.Timedelta(hours=min_rebalance_hours)

    grid_epochs = []
    pnl_data = []
    fills = []

    current_lower = None
    current_upper = None
    current_spacing = None
    current_levels = []
    last_rebalance_time = None
    buy_orders = set()
    sell_orders = set()
    # open_positions: sell_level -> (buy_price, position_amount)
    # Trackt offene Long-Positionen (Buy gefüllt, Sell noch offen)
    # Nötig für korrekte Mark-to-Market-Berechnung und realistischen DD
    open_positions: dict = {}
    total_pnl = 0.0
    amount = 0.0

    def calc_fib(window_df, ref_price):
        """Fibonacci aus einem OHLCV-Fenster berechnen."""
        swing = find_swing_high_low(window_df, swing_window=swing_window)
        fib_levels = calculate_fib_levels(swing['swing_low'], swing['swing_high'])
        suggested = find_best_grid_range(fib_levels, ref_price, prefer_golden_zone)
        return suggested, fib_levels, swing

    def setup_grid(lower, upper, ref_price):
        """Grid-Orders fuer neuen Bereich aufsetzen."""
        nonlocal amount
        spacing = (upper - lower) / num_grids
        levels = [_r(lower + j * spacing) for j in range(num_grids + 1)]
        mid = (upper + lower) / 2.0
        amount = (capital * leverage) / (num_grids * mid)
        buys = {l for l in levels if l < ref_price}
        sells = {l for l in levels if l > ref_price}
        return spacing, levels, buys, sells

    df_arr = list(df.iterrows())
    n = len(df_arr)

    for i, (ts, row) in enumerate(df_arr):
        price = float(row['close'])
        candle_low = float(row['low'])
        candle_high = float(row['high'])

        # ---- Initialisierung oder Rebalancing pruefen ----
        need_rebalance = False

        if current_lower is None:
            # Erstinitialisierung: warte bis lookback_fib Kerzen vorhanden
            if i < lookback_fib:
                pnl_data.append({'timestamp': ts, 'pnl': 0.0, 'capital': capital})
                continue
            need_rebalance = True

        elif not (current_lower <= price <= current_upper):
            # Preis ausserhalb der Range
            cooldown_ok = (last_rebalance_time is None or
                           (ts - last_rebalance_time) >= min_rebalance_td)
            if cooldown_ok:
                need_rebalance = True

        if need_rebalance:
            # Fibonacci aus den letzten lookback_fib Kerzen berechnen
            window = df.iloc[max(0, i - lookback_fib):i]
            try:
                suggested, fib_levels, swing = calc_fib(window, price)
                new_lower = suggested['lower_price']
                new_upper = suggested['upper_price']

                if new_upper > new_lower and new_upper > 0:
                    # Aktuelle Epoche abschliessen
                    if grid_epochs:
                        grid_epochs[-1]['end_ts'] = ts

                    # Offene Positionen zum aktuellen Preis schliessen (Rebalancing = neue Epoche).
                    # Live-Bot cancelt alle Orders, Positionen bleiben offen; hier buchen wir
                    # den unrealisierten P&L ein damit keine Zombie-Positionen akkumulieren.
                    for _sp_key, (bp, pos_amt) in list(open_positions.items()):
                        total_pnl += (price - bp) * pos_amt
                    open_positions.clear()

                    new_spacing, new_levels, new_buys, new_sells = setup_grid(new_lower, new_upper, price)

                    grid_epochs.append({
                        'start_ts': ts,
                        'end_ts': None,   # wird beim naechsten Rebalancing gesetzt
                        'lower': new_lower,
                        'upper': new_upper,
                        'lower_label': suggested['lower_label'],
                        'upper_label': suggested['upper_label'],
                        'fib_levels': fib_levels,
                        'swing': swing,
                        'spacing': new_spacing,
                    })

                    current_lower = new_lower
                    current_upper = new_upper
                    current_spacing = new_spacing
                    current_levels = new_levels
                    buy_orders = new_buys
                    sell_orders = new_sells
                    last_rebalance_time = ts

            except Exception:
                pnl_data.append({'timestamp': ts, 'pnl': total_pnl, 'capital': capital + total_pnl})
                continue

        # ---- Grid-Fills simulieren ----
        if current_spacing and amount > 0:
            new_sells = set()
            new_buys = set()
            fee_rate = DEFAULT_FEE_RATE

            for bp in list(buy_orders):
                if candle_low <= bp:
                    total_pnl -= bp * amount * fee_rate
                    buy_orders.discard(bp)
                    sp = _r(bp + current_spacing)
                    if sp <= _r(current_upper) + 1e-9:
                        new_sells.add(sp)
                        open_positions[sp] = (bp, amount)  # offene Long-Position
                    fills.append({'timestamp': ts, 'price': bp, 'side': 'buy'})

            for sp in list(sell_orders):
                if candle_high >= sp:
                    total_pnl += current_spacing * amount - sp * amount * fee_rate
                    sell_orders.discard(sp)
                    open_positions.pop(sp, None)  # Position geschlossen
                    bp = _r(sp - current_spacing)
                    if bp >= _r(current_lower) - 1e-9:
                        new_buys.add(bp)
                    fills.append({'timestamp': ts, 'price': sp, 'side': 'sell'})

            sell_orders.update(new_sells - sell_orders)
            buy_orders.update(new_buys - buy_orders)

        # Mark-to-Market: unrealisierte Verluste offener Long-Positionen einrechnen
        unrealized = sum((price - bp) * pos_amt
                         for _sp, (bp, pos_amt) in open_positions.items())
        pnl_data.append({'timestamp': ts,
                         'pnl': round(total_pnl + unrealized, 4),
                         'capital': round(capital + total_pnl + unrealized, 4)})

    # Letzte Epoche abschliessen
    if grid_epochs and grid_epochs[-1]['end_ts'] is None:
        grid_epochs[-1]['end_ts'] = df.index[-1]

    pnl_df = pd.DataFrame(pnl_data).set_index('timestamp') if pnl_data else pd.DataFrame()
    fills_df = pd.DataFrame(fills).set_index('timestamp') if fills else pd.DataFrame()
    return grid_epochs, pnl_df, fills_df


# ---------------------------------------------------------------------------
# Plotly-Chart erstellen
# ---------------------------------------------------------------------------

def create_chart(symbol: str, timeframe: str, df: pd.DataFrame,
                 grid_epochs: list, pnl_df: pd.DataFrame, fills_df: pd.DataFrame,
                 capital: float, num_grids: int, leverage: int,
                 start_date=None, end_date=None, window=None):
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        logger.error("plotly nicht installiert. Bitte: pip install plotly")
        return None

    # Zeitraum-Filter
    def _filter(frame, col=None):
        if frame.empty:
            return frame
        f = frame
        if window:
            cutoff = datetime.now(timezone.utc) - timedelta(days=window)
            f = f[f.index >= cutoff]
        if start_date:
            f = f[f.index >= pd.to_datetime(start_date, utc=True)]
        if end_date:
            f = f[f.index <= pd.to_datetime(end_date, utc=True)]
        return f

    if window:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window)
        df = df[df.index >= cutoff].copy()
    if start_date:
        df = df[df.index >= pd.to_datetime(start_date, utc=True)]
    if end_date:
        df = df[df.index <= pd.to_datetime(end_date, utc=True)]
    pnl_df = _filter(pnl_df)
    fills_df = _filter(fills_df)

    # Kontostand auf Eingabe-Kapital normalisieren (Simulation laeuft ab dem ersten Datenpunkt,
    # aber der Chart-Zeitraum kann spaeter beginnen → Offset entfernen)
    if not pnl_df.empty and 'capital' in pnl_df.columns:
        offset = pnl_df['capital'].iloc[0] - capital
        pnl_df = pnl_df.copy()
        pnl_df['capital'] = pnl_df['capital'] - offset
        pnl_df['pnl']     = pnl_df['capital'] - capital

    if df.empty:
        logger.warning(f"Keine Daten im Zeitraum fuer {symbol}")
        return None

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

    # ===== GRID-EPOCHEN =====
    # Performance: statt tausende add_shape()-Aufrufe → Scatter-Traces mit None-Trennern
    epoch_color_lines = ['#1d4ed8', '#7c3aed', '#0891b2', '#059669', '#d97706']
    epoch_color_fills = [
        'rgba(37,99,235,0.10)', 'rgba(124,58,237,0.10)',
        'rgba(8,145,178,0.10)', 'rgba(5,150,105,0.10)', 'rgba(217,119,6,0.10)',
    ]

    visible_start = df.index.min()
    visible_end   = df.index.max()

    shown_epochs = [
        e for e in grid_epochs
        if e['end_ts'] is not None and
           pd.Timestamp(e['end_ts']) >= visible_start and
           pd.Timestamp(e['start_ts']) <= visible_end
    ]

    logger.info(f"  {len(shown_epochs)} sichtbare Epochen werden gerendert...")

    # Epochen pro Farbe in je einen Scatter-Fill-Trace bündeln (5 Traces statt N shapes)
    fill_xs = [[] for _ in range(5)]
    fill_ys = [[] for _ in range(5)]
    # Innere Grid-Linien: 1 Scatter-Trace (dashed, alle Epochen zusammen)
    inner_xs: list = []
    inner_ys: list = []
    # Rand-Linien: 1 Scatter-Trace (solid)
    border_xs: list = []
    border_ys: list = []

    rebalance_x = []
    rebalance_y = []
    annotations = []

    total_duration = (visible_end - visible_start).total_seconds()

    for idx, epoch in enumerate(shown_epochs):
        ci = idx % 5
        x0 = max(pd.Timestamp(epoch['start_ts']), visible_start)
        x1 = min(pd.Timestamp(epoch['end_ts']), visible_end)
        lower = epoch['lower']
        upper = epoch['upper']
        spacing = epoch['spacing']

        # Gefülltes Rechteck als Scatter-Polygon (x0,y0 → x1,y0 → x1,y1 → x0,y1 → x0,y0)
        fill_xs[ci].extend([x0, x1, x1, x0, x0, None])
        fill_ys[ci].extend([lower, lower, upper, upper, lower, None])

        # Rand-Linien (oben + unten, solid)
        border_xs.extend([x0, x1, None, x0, x1, None])
        border_ys.extend([lower, lower, None, upper, upper, None])

        # Innere Grid-Levels (nur zeichnen wenn ≤ 100 Epochen sichtbar)
        if len(shown_epochs) <= 100:
            for j in range(1, num_grids):
                gp = lower + j * spacing
                inner_xs.extend([x0, x1, None])
                inner_ys.extend([gp, gp, None])

        # Fibonacci-Label (nur für lange Epochen)
        duration = (pd.Timestamp(x1) - pd.Timestamp(x0)).total_seconds()
        if total_duration > 0 and duration / total_duration > 0.05:
            annotations.append(dict(
                x=x0, y=upper,
                text=f" {epoch['upper_label']}",
                showarrow=False, xanchor='left',
                font=dict(size=9, color=epoch_color_lines[ci]), yref='y',
            ))
            annotations.append(dict(
                x=x0, y=lower,
                text=f" {epoch['lower_label']}",
                showarrow=False, xanchor='left',
                font=dict(size=9, color=epoch_color_lines[ci]), yref='y',
            ))

        # Rebalancing-Marker
        if idx > 0:
            rebalance_x.append(pd.Timestamp(epoch['start_ts']))
            rebalance_y.append((lower + upper) / 2)

    # Gefüllte Band-Traces hinzufügen (max 5 Traces)
    # Legende: erster sichtbarer Farb-Trace bekommt erklärendes Label
    legend_added = False
    for ci in range(5):
        if fill_xs[ci]:
            show = not legend_added
            legend_added = True
            fig.add_trace(go.Scatter(
                x=fill_xs[ci], y=fill_ys[ci],
                fill='toself',
                fillcolor=epoch_color_fills[ci],
                line=dict(color=epoch_color_lines[ci], width=0),
                mode='lines',
                showlegend=show,
                name='Grid-Epoche (je Farbe = 1 Fib-Range)' if show else None,
                hoverinfo='skip',
            ), secondary_y=False)

    # Rand-Linien (ein Trace)
    if border_xs:
        fig.add_trace(go.Scatter(
            x=border_xs, y=border_ys, mode='lines',
            line=dict(color='rgba(100,100,200,0.5)', width=1),
            showlegend=False, hoverinfo='skip',
        ), secondary_y=False)

    # Innere Grid-Linien (ein Trace)
    if inner_xs:
        fig.add_trace(go.Scatter(
            x=inner_xs, y=inner_ys, mode='lines',
            line=dict(color='rgba(100,100,200,0.3)', width=0.5, dash='dot'),
            showlegend=False, hoverinfo='skip',
        ), secondary_y=False)

    # Rebalancing-Punkte
    if rebalance_x:
        fig.add_trace(go.Scatter(
            x=rebalance_x, y=rebalance_y, mode='markers',
            marker=dict(color='#f59e0b', symbol='diamond', size=8,
                        line=dict(width=1, color='#92400e')),
            name='Rebalancing',
            showlegend=True,
        ), secondary_y=False)

    # ===== BUY / SELL FILLS =====
    if not fills_df.empty and 'side' in fills_df.columns and 'price' in fills_df.columns:
        buys  = fills_df[fills_df['side'] == 'buy']
        sells = fills_df[fills_df['side'] == 'sell']
        if not buys.empty:
            fig.add_trace(go.Scatter(
                x=buys.index, y=buys['price'],
                mode='markers',
                marker=dict(color='#16a34a', symbol='triangle-up', size=7,
                            line=dict(width=0.5, color='#14532d')),
                name='Kauf (Long-Entry / Short-Exit)',
                showlegend=True,
            ), secondary_y=False)
        if not sells.empty:
            fig.add_trace(go.Scatter(
                x=sells.index, y=sells['price'],
                mode='markers',
                marker=dict(color='#dc2626', symbol='triangle-down', size=7,
                            line=dict(width=0.5, color='#7f1d1d')),
                name='Verkauf (Short-Entry / Long-Exit)',
                showlegend=True,
            ), secondary_y=False)

    # ===== KONTOSTAND (rechte Y-Achse) =====
    if not pnl_df.empty and 'capital' in pnl_df.columns:
        fig.add_trace(go.Scatter(
            x=pnl_df.index, y=pnl_df['capital'],
            name='Kontostand',
            line=dict(color='#2563eb', width=2),
            opacity=0.85,
            showlegend=True,
        ), secondary_y=True)

    # ===== TITEL =====
    n_epochs = len(shown_epochs)
    total_pnl = pnl_df['pnl'].iloc[-1] if not pnl_df.empty and 'pnl' in pnl_df.columns else 0
    roi = total_pnl / capital * 100 if capital > 0 else 0
    title_text = (
        f"{symbol} {timeframe} - gbot Grid (dynamisch) | "
        f"Kapital: {capital:.0f} USDT | "
        f"PnL: {total_pnl:+.2f} USDT ({roi:+.1f}%) | "
        f"Grids: {num_grids} | Hebel: {leverage}x | "
        f"Rebalancings: {n_epochs - 1 if n_epochs > 0 else 0}"
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
        annotations=annotations,
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

    from gbot.analysis.fibonacci import fetch_ohlcv_public
    from gbot.analysis.optimizer import LOOKBACK_BY_TF, DEFAULT_LOOKBACK

    for filename, filepath in selected_configs:
        try:
            with open(filepath, 'r') as f:
                cfg = json.load(f)

            symbol    = cfg['market']['symbol']
            fib_cfg   = cfg['grid'].get('fibonacci', {})
            tf        = fib_cfg.get('timeframe', '4h')
            lookback  = LOOKBACK_BY_TF.get(tf, DEFAULT_LOOKBACK)
            num_grids = cfg['grid']['num_grids']
            leverage  = cfg['risk'].get('leverage', 1)
            lookback_fib       = fib_cfg.get('lookback', 200)
            swing_window       = fib_cfg.get('swing_window', 10)
            prefer_golden      = fib_cfg.get('prefer_golden_zone', False)
            min_rebalance_h    = fib_cfg.get('min_rebalance_interval_hours', 4)

            logger.info(f"\nVerarbeite {symbol} ({tf})...")

            logger.info("Lade OHLCV-Daten...")
            df = fetch_ohlcv_public(symbol, tf, lookback)

            if df.empty:
                logger.warning(f"Keine Daten fuer {symbol}")
                continue

            logger.info(f"  {len(df)} Kerzen geladen. Simuliere dynamisches Grid...")

            grid_epochs, pnl_df, fills_df = simulate_dynamic_grid(
                df=df,
                num_grids=num_grids,
                leverage=leverage,
                capital=capital,
                lookback_fib=lookback_fib,
                swing_window=swing_window,
                prefer_golden_zone=prefer_golden,
                min_rebalance_hours=min_rebalance_h,
            )

            logger.info(f"  {len(grid_epochs)} Grid-Epochen, "
                        f"{len(grid_epochs) - 1 if grid_epochs else 0} Rebalancings")

            logger.info("Erstelle interaktiven Chart...")
            fig = create_chart(
                symbol=symbol, timeframe=tf, df=df,
                grid_epochs=grid_epochs, pnl_df=pnl_df, fills_df=fills_df,
                capital=capital, num_grids=num_grids, leverage=leverage,
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
