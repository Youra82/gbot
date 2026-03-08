#!/bin/bash
# run_pipeline.sh — gbot Grid-Konfigurations-Assistent

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$SCRIPT_DIR"

VENV_PYTHON=".venv/bin/python3"
CONFIGS_DIR="src/gbot/strategy/configs"
SETTINGS_FILE="settings.json"
FIB_SCRIPT="src/gbot/analysis/fibonacci.py"
TMP_FIB="/tmp/gbot_fib_result.json"

echo -e "${BLUE}======================================================="
echo "         gbot — Grid Konfigurations-Assistent"
echo -e "=======================================================${NC}"

if [ ! -f "$VENV_PYTHON" ]; then
    echo -e "${RED}Virtuelle Umgebung nicht gefunden. Bitte install.sh ausfuehren.${NC}"
    exit 1
fi

source ".venv/bin/activate"
mkdir -p "$CONFIGS_DIR"

# -------------------------------------------------------
# 1. Symbol
# -------------------------------------------------------
echo -e "\n${YELLOW}Symbol eingeben (z.B. BTC, ETH, SOL) [Standard: BTC]:${NC}"
read -p "> " SYM_INPUT
SYM_INPUT=${SYM_INPUT:-BTC}
SYMBOL="${SYM_INPUT^^}/USDT:USDT"
echo -e "${GREEN}Symbol: $SYMBOL${NC}"

# -------------------------------------------------------
# 2. Grid-Modus
# -------------------------------------------------------
echo -e "\n${YELLOW}Grid-Modus:${NC}"
echo "  1) neutral  — Buy unter, Sell ueber aktuellem Preis  (Standard)"
echo "  2) long     — Nur Kauf-Orders"
echo "  3) short    — Nur Verkauf-Orders"
read -p "> [Standard: 1]: " MODE_CHOICE
MODE_CHOICE=${MODE_CHOICE:-1}
case "$MODE_CHOICE" in
    2) GRID_MODE="long" ;;
    3) GRID_MODE="short" ;;
    *) GRID_MODE="neutral" ;;
esac
echo -e "${GREEN}Modus: $GRID_MODE${NC}"

# -------------------------------------------------------
# 3. Anzahl Grid-Stufen
# -------------------------------------------------------
echo -e "\n${YELLOW}Anzahl Grid-Stufen [Standard: 10]:${NC}"
read -p "> " NUM_GRIDS
NUM_GRIDS=${NUM_GRIDS:-10}

# -------------------------------------------------------
# 4. Kapital & Risiko
# -------------------------------------------------------
echo -e "\n${YELLOW}Kapital in USDT [Standard: 100]:${NC}"
read -p "> " INVESTMENT
INVESTMENT=${INVESTMENT:-100}

echo -e "${YELLOW}Hebel [Standard: 3]:${NC}"
read -p "> " LEVERAGE
LEVERAGE=${LEVERAGE:-3}

echo -e "${YELLOW}Margin-Modus (isolated/cross) [Standard: isolated]:${NC}"
read -p "> " MARGIN_MODE
MARGIN_MODE=${MARGIN_MODE:-isolated}

# -------------------------------------------------------
# 5. Preisbereich: Fibonacci (auto) oder Manuell
# -------------------------------------------------------
echo -e "\n${YELLOW}Preisbereich:${NC}"
echo "  1) Fibonacci automatisch berechnen  (Standard)"
echo "  2) Manuell eingeben"
read -p "> [Standard: 1]: " RANGE_MODE
RANGE_MODE=${RANGE_MODE:-1}

FIB_TF="4h"
FIB_LOOKBACK=200
FIB_WINDOW=10

if [ "$RANGE_MODE" == "2" ]; then
    # Manuell
    echo -e "\n${YELLOW}Untere Preisgrenze:${NC}"
    read -p "> " LOWER_PRICE
    echo -e "${YELLOW}Obere Preisgrenze:${NC}"
    read -p "> " UPPER_PRICE
else
    # Fibonacci — rechnet automatisch, keine weiteren Fragen
    echo -e "\n${CYAN}Berechne Fibonacci Retracement fuer $SYMBOL...${NC}"

    "$VENV_PYTHON" "$FIB_SCRIPT" \
        --symbol "$SYMBOL" \
        --timeframe "$FIB_TF" \
        --lookback "$FIB_LOOKBACK" \
        --swing_window "$FIB_WINDOW"

    if [ $? -ne 0 ]; then
        echo -e "${RED}Fibonacci fehlgeschlagen. Bitte Preisbereich manuell eingeben:${NC}"
        echo -e "${YELLOW}Untere Preisgrenze:${NC}"
        read -p "> " LOWER_PRICE
        echo -e "${YELLOW}Obere Preisgrenze:${NC}"
        read -p "> " UPPER_PRICE
        RANGE_MODE="2"
    else
        # Werte fuer Grid-Vorschau holen (nur zur Anzeige — Bot rechnet live)
        "$VENV_PYTHON" "$FIB_SCRIPT" \
            --symbol "$SYMBOL" \
            --timeframe "$FIB_TF" \
            --lookback "$FIB_LOOKBACK" \
            --swing_window "$FIB_WINDOW" \
            --json > "$TMP_FIB" 2>/dev/null

        LOWER_PRICE=$("$VENV_PYTHON" -c "import json; d=json.load(open('$TMP_FIB')); print(d['suggested_lower'])")
        UPPER_PRICE=$("$VENV_PYTHON" -c "import json; d=json.load(open('$TMP_FIB')); print(d['suggested_upper'])")
    fi
fi

# -------------------------------------------------------
# 6. Grid-Vorschau (info only)
# -------------------------------------------------------
echo -e "\n${BLUE}======================================================="
echo "  Grid-Vorschau"
echo -e "=======================================================${NC}"

"$VENV_PYTHON" - <<PYEOF
import sys
sys.path.insert(0, 'src')
from gbot.strategy.grid_logic import (
    calculate_grid_levels, get_grid_spacing,
    calculate_amount_per_grid, estimate_grid_roi, format_grid_summary
)

lower  = float("$LOWER_PRICE")
upper  = float("$UPPER_PRICE")
grids  = int("$NUM_GRIDS")
mode   = "$GRID_MODE"
lev    = int("$LEVERAGE")
inv    = float("$INVESTMENT")
sym    = "$SYMBOL"
fib    = "$RANGE_MODE" != "2"

try:
    levels  = calculate_grid_levels(lower, upper, grids)
    spacing = get_grid_spacing(lower, upper, grids)
    mid     = (lower + upper) / 2
    amount  = calculate_amount_per_grid(inv, grids, mid, lev)
    roi     = estimate_grid_roi(lower, upper, grids, inv, lev)

    print(format_grid_summary(sym, lower, upper, grids, spacing, amount, mode, lev, inv))
    print()
    if fib:
        print("  (Vorschau basiert auf aktuellem Fibonacci-Stand.)")
        print("  Der Live-Bot berechnet den Bereich bei jedem Start neu.")
        print()
    print(f"  Spacing %     : {spacing / mid * 100:.3f}%")
    print(f"  Profit/Zyklus : ~{roi['profit_per_cycle_usdt']:.4f} USDT")
    print(f"  Tages-ROI     : ~{roi['daily_roi_pct_estimate']:.4f}%")
except Exception as e:
    print(f"Fehler: {e}")
    sys.exit(1)
PYEOF

if [ $? -ne 0 ]; then
    echo -e "${RED}Fehler in der Grid-Vorschau. Eingaben pruefen.${NC}"
    deactivate
    exit 1
fi

# -------------------------------------------------------
# 7. Bestaetigung & Config speichern
# -------------------------------------------------------
echo -e "\n${YELLOW}Config speichern?${NC}"
read -p "(j/n) [Standard: j]: " CONFIRM
CONFIRM=${CONFIRM:-j}

if [[ "$CONFIRM" != "j" && "$CONFIRM" != "J" ]]; then
    echo -e "${YELLOW}Abgebrochen.${NC}"
    deactivate
    exit 0
fi

SAFE_SYMBOL=$(echo "$SYMBOL" | sed 's|/|_|g; s|:||g')
CONFIG_FILE="$CONFIGS_DIR/config_${SAFE_SYMBOL}.json"

"$VENV_PYTHON" - <<PYEOF
import json, os

USE_FIB = "$RANGE_MODE" != "2"

grid_section = {
    "num_grids": int("$NUM_GRIDS"),
    "grid_mode": "$GRID_MODE",
}

if USE_FIB:
    grid_section["fibonacci"] = {
        "enabled": True,
        "timeframe": "$FIB_TF",
        "lookback": int("$FIB_LOOKBACK"),
        "swing_window": int("$FIB_WINDOW"),
        "prefer_golden_zone": False,
        "rebalance_on_break": True,
        "min_rebalance_interval_hours": 4
    }
else:
    grid_section["lower_price"] = float("$LOWER_PRICE")
    grid_section["upper_price"] = float("$UPPER_PRICE")
    grid_section["fibonacci"] = {"enabled": False}

config = {
    "market": {"symbol": "$SYMBOL"},
    "grid": grid_section,
    "risk": {
        "total_investment_usdt": float("$INVESTMENT"),
        "leverage": int("$LEVERAGE"),
        "margin_mode": "$MARGIN_MODE"
    }
}

os.makedirs(os.path.dirname("$CONFIG_FILE"), exist_ok=True)
with open("$CONFIG_FILE", "w") as f:
    json.dump(config, f, indent=4)
print(f"Config gespeichert: $CONFIG_FILE")

try:
    with open("$SETTINGS_FILE", "r") as f:
        settings = json.load(f)
except:
    settings = {"live_trading_settings": {"active_strategies": []}}

strategies = settings.setdefault("live_trading_settings", {}).setdefault("active_strategies", [])
if not any(s.get("symbol") == "$SYMBOL" for s in strategies):
    strategies.append({"symbol": "$SYMBOL", "active": True})
    with open("$SETTINGS_FILE", "w") as f:
        json.dump(settings, f, indent=4)
    print("settings.json: $SYMBOL hinzugefuegt.")
else:
    print("settings.json: $SYMBOL bereits vorhanden.")
PYEOF

echo -e "\n${GREEN}======================================================="
echo "  Fertig!"
echo ""
echo "  Config: $CONFIG_FILE"
echo ""
echo "Naechste Schritte:"
echo "  ./run_tests.sh"
echo "  .venv/bin/python3 master_runner.py"
echo "  Cronjob: */5 * * * * .venv/bin/python3 master_runner.py"
echo -e "=======================================================${NC}"

rm -f "$TMP_FIB"
deactivate
