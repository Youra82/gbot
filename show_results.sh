#!/bin/bash
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$SCRIPT_DIR"

VENV_PATH=".venv/bin/activate"
RESULTS_SCRIPT="src/gbot/analysis/show_results.py"

if [ ! -f "$VENV_PATH" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausfuehren.${NC}"
    exit 1
fi

source "$VENV_PATH"

if [ ! -f "$RESULTS_SCRIPT" ]; then
    echo -e "${RED}Fehler: Die Analyse-Datei '$RESULTS_SCRIPT' wurde nicht gefunden.${NC}"
    deactivate
    exit 1
fi

# --- MODUS-MENUE ---
echo -e "\n${YELLOW}Waehle einen Analyse-Modus fuer gbot:${NC}"
echo "  1) Einzel-Analyse                (jede Strategie wird isoliert getestet)"
echo "  2) Manuelle Portfolio-Simulation  (du waehlst das Team)"
echo "  3) Automatische Portfolio-Optim. (der Bot waehlt das beste Team)"
echo "  4) Interaktive Charts            (Fibonacci-Zonen + Grid-Levels)"
read -p "Auswahl (1-4) [Standard: 1]: " MODE

# Validierung
if [[ ! "$MODE" =~ ^[1-4]?$ ]]; then
    echo -e "${RED}Ungueltige Eingabe! Verwende Standard (1).${NC}"
    MODE=1
fi
MODE=${MODE:-1}

# Max Drawdown fuer Modus 3
TARGET_MAX_DD=30
if [ "$MODE" == "3" ]; then
    read -p "Maximaler Drawdown pro Grid in % [Standard: 30]: " DD_INPUT
    if [[ "$DD_INPUT" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        TARGET_MAX_DD=$DD_INPUT
    else
        echo "Ungueltige Eingabe, verwende Standard: ${TARGET_MAX_DD}%"
    fi
fi

python3 "$RESULTS_SCRIPT" --mode "$MODE" --target_max_drawdown "$TARGET_MAX_DD"
EXIT_CODE=$?

# --- MODUS 4: Charts fertig ---
if [ "$MODE" == "4" ]; then
    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}Charts erfolgreich generiert.${NC}"
    else
        echo -e "${RED}Fehler beim Generieren der Charts.${NC}"
    fi
    deactivate
    exit $EXIT_CODE
fi

# --- MODUS 3: Optimales Portfolio in settings.json eintragen? ---
if [ "$MODE" == "3" ]; then
    OPTIMIZATION_FILE="artifacts/results/optimization_results.json"
    if [ -f "$OPTIMIZATION_FILE" ]; then
        echo ""
        echo -e "${YELLOW}─────────────────────────────────────────────────${NC}"
        read -p "Sollen die optimalen Strategien in settings.json eingetragen werden? (j/n): " AUTO_UPDATE
        AUTO_UPDATE="${AUTO_UPDATE//[$'\r\n ']/}"

        if [[ "$AUTO_UPDATE" == "j" || "$AUTO_UPDATE" == "J" || "$AUTO_UPDATE" == "y" || "$AUTO_UPDATE" == "Y" ]]; then
            echo -e "${BLUE}Uebertrage Ergebnisse nach settings.json...${NC}"

            python3 << 'EOF'
import json, re

with open('artifacts/results/optimization_results.json', 'r') as f:
    opt = json.load(f)

portfolio = opt.get('optimal_portfolio', [])
if not portfolio:
    print("Kein optimales Portfolio gefunden. settings.json unveraendert.")
else:
    strategies = []
    for filename in portfolio:
        # config_BTC_USDT_USDT.json -> BTC/USDT:USDT
        m = re.match(r'config_([A-Z0-9]+)_USDT_USDT\.json', filename)
        if m:
            coin = m.group(1)
            strategies.append({'symbol': f"{coin}/USDT:USDT", 'active': True})

    with open('settings.json', 'r') as f:
        settings = json.load(f)

    settings.setdefault('live_trading_settings', {})['active_strategies'] = strategies

    with open('settings.json', 'w') as f:
        json.dump(settings, f, indent=4)

    print(f"{len(strategies)} Strategien in settings.json eingetragen:")
    for s in strategies:
        print(f"   - {s['symbol']}")
EOF

            echo -e "${GREEN}settings.json erfolgreich aktualisiert.${NC}"
        else
            echo -e "${YELLOW}Keine Aenderungen an settings.json vorgenommen.${NC}"
        fi
    fi
fi

deactivate
