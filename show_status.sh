#!/bin/bash
# show_status.sh — Live Grid-Status & Vollständige Code-Dokumentation

BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$SCRIPT_DIR"

VENV_PYTHON=".venv/bin/python3"
TRACKER_DIR="artifacts/tracker"

echo -e "${BLUE}======================================================="
echo "         gbot — Live Status Uebersicht"
echo -e "=======================================================${NC}"

# --- settings.json anzeigen ---
echo -e "\n${YELLOW}--- Aktive Strategien (settings.json) ---${NC}"
if [ -f "settings.json" ]; then
    if command -v jq &> /dev/null; then
        jq '.live_trading_settings.active_strategies' settings.json
    else
        cat settings.json
    fi
else
    echo -e "${RED}settings.json nicht gefunden.${NC}"
fi

# --- Tracker-Status ---
echo -e "\n${YELLOW}--- Grid Tracker-Status ---${NC}"

if [ ! -d "$TRACKER_DIR" ] || [ -z "$(ls -A $TRACKER_DIR 2>/dev/null)" ]; then
    echo -e "${CYAN}Keine Tracker-Dateien vorhanden. Grid wurde noch nicht gestartet.${NC}"
else
    if [ -f "$VENV_PYTHON" ]; then
        source ".venv/bin/activate" 2>/dev/null
        "$VENV_PYTHON" - <<'PYEOF'
import json, os, glob
from datetime import datetime, timezone

TRACKER_DIR = "artifacts/tracker"
files = sorted(glob.glob(os.path.join(TRACKER_DIR, "*_grid.json")))

if not files:
    print("  Keine Tracker-Dateien gefunden.")
else:
    for path in files:
        try:
            with open(path) as f:
                t = json.load(f)
        except Exception as e:
            print(f"  Fehler beim Lesen von {path}: {e}")
            continue

        symbol = t.get("symbol", "Unbekannt")
        init = t.get("initialized", False)
        init_at = t.get("initialized_at", "—")
        gc = t.get("grid_config", {})
        perf = t.get("performance", {})
        orders = t.get("active_orders", {})

        print(f"\n  {'='*55}")
        print(f"  Symbol  : {symbol}")
        print(f"  Status  : {'Aktiv' if init else 'Nicht initialisiert'}")
        print(f"  Gestartet: {init_at}")
        if gc:
            print(f"  Grid    : {gc.get('lower_price')} - {gc.get('upper_price')} | {gc.get('num_grids')} Stufen | Modus: {gc.get('mode','?').upper()}")
            print(f"  Spacing : {gc.get('spacing', 0):.4f} | Menge/Grid: {gc.get('amount_per_grid', 0):.6f}")
            print(f"  Hebel   : {gc.get('leverage','?')}x {gc.get('margin_mode','?')}")
            print(f"  Kapital : {gc.get('total_investment_usdt','?')} USDT")
        print(f"  --- Offene Orders ({len(orders)}) ---")
        if orders:
            buy_count = sum(1 for o in orders.values() if o.get("side") == "buy")
            sell_count = sum(1 for o in orders.values() if o.get("side") == "sell")
            prices = sorted(float(p) for p in orders.keys())
            print(f"    Buy-Orders : {buy_count}")
            print(f"    Sell-Orders: {sell_count}")
            print(f"    Preis-Range: {min(prices):.4f} - {max(prices):.4f}")
        else:
            print("    (keine offenen Orders im Tracker)")
        print(f"  --- Performance ---")
        print(f"    Fills gesamt   : {perf.get('total_fills', 0)}")
        print(f"    Buy-Fills      : {perf.get('buy_fills', 0)}")
        print(f"    Sell-Fills     : {perf.get('sell_fills', 0)}")
        pnl = perf.get('realized_pnl_usdt', 0.0)
        pnl_str = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
        print(f"    Realized PnL   : {pnl_str} USDT")
        print(f"    Gezahlte Fees  : {perf.get('fee_paid_usdt', 0.0):.4f} USDT")
        last_fill = perf.get('last_fill_at')
        print(f"    Letzter Fill   : {last_fill if last_fill else '—'}")
        print(f"  {'='*55}")

PYEOF
        deactivate 2>/dev/null
    else
        echo -e "${YELLOW}Python venv nicht gefunden — zeige rohe JSON-Dateien:${NC}"
        for f in "$TRACKER_DIR"/*_grid.json; do
            echo -e "\n${CYAN}$f:${NC}"
            cat "$f"
        done
    fi
fi

# --- Projekt-Struktur ---
echo -e "\n\n${BLUE}======================================================="
echo "           Projektstruktur (gbot)"
echo -e "=======================================================${NC}"

find . -path './.venv' -prune -o \
       -path './.git' -prune -o \
       -path '*/__pycache__' -prune -o \
       -path './data/cache' -prune -o \
       -path './artifacts/tracker' -prune -o \
       -path './logs' -prune -o \
       -maxdepth 4 -print \
| sed -e 's;[^/]*/;|____;g;s;____|; |;g'

# --- Code-Dokumentation ---
echo -e "\n\n${BLUE}======================================================="
echo "           Vollstaendige Code-Dokumentation (gbot)"
echo -e "=======================================================${NC}"

show_file_content() {
    local FILE=$1
    if [ -f "$FILE" ]; then
        echo -e "\n${BLUE}======================================================================"
        echo -e "${YELLOW}DATEI: $FILE${NC}"
        echo -e "${BLUE}----------------------------------------------------------------------${NC}"
        cat -n "$FILE"
        echo -e "${BLUE}======================================================================${NC}"
    fi
}

mapfile -t FILE_LIST < <(
    find . -path './.venv' -prune -o \
           -path './.git' -prune -o \
           -path './secret.json' -prune -o \
           -path '*/__pycache__' -prune -o \
           -path './data' -prune -o \
           -path './artifacts' -prune -o \
           -path './logs' -prune -o \
           \( -name "*.py" -o -name "*.sh" -o -name "*.json" -o -name "*.txt" \) \
           -print | sed 's|^\./||' | sort
)

for filepath in "${FILE_LIST[@]}"; do
    if [ -f "$filepath" ]; then
        show_file_content "$filepath"
    fi
done

# secret.json zensiert anzeigen
if [ -f "secret.json" ]; then
    echo -e "\n${BLUE}======================================================================"
    echo -e "${YELLOW}DATEI: secret.json (ZENSIERT)${NC}"
    echo -e "${BLUE}----------------------------------------------------------------------${NC}"
    if command -v jq &> /dev/null; then
        jq '(.gbot[]? | .apiKey, .secret, .password) |= "[ZENSIERT]" |
            (.telegram?.bot_token) |= "[ZENSIERT]" |
            (.telegram?.chat_id) |= "[ZENSIERT]"' secret.json | cat -n
    else
        sed -E 's/("apiKey"|"secret"|"password"|"bot_token"|"chat_id"): ".*"/\1: "[ZENSIERT]"/g' secret.json | cat -n
    fi
    echo -e "${BLUE}======================================================================${NC}"
fi

echo -e "\n${BLUE}=======================================================${NC}"
