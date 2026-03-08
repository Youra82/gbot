#!/bin/bash
# run_pipeline.sh — gbot Optimierungs-Pipeline

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$SCRIPT_DIR"

VENV_PATH=".venv/bin/activate"
OPTIMIZER="src/gbot/analysis/optimizer.py"
SETTINGS="settings.json"

echo -e "${BLUE}======================================================="
echo "       gbot — Grid Optimierungs-Pipeline"
echo -e "=======================================================${NC}"

# --- Virtuelle Umgebung ---
if [ ! -f "$VENV_PATH" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausfuehren.${NC}"
    exit 1
fi
source "$VENV_PATH"
echo -e "${GREEN}Virtuelle Umgebung aktiviert.${NC}"

# --- Aufraeum-Assistent ---
echo -e "\n${YELLOW}Moechtest du alle alten Configs vor dem Start loeschen?${NC}"
read -p "Empfohlen fuer kompletten Neustart. (j/n) [Standard: n]: " CLEANUP
CLEANUP=${CLEANUP:-n}
if [[ "$CLEANUP" == "j" || "$CLEANUP" == "J" ]]; then
    rm -f src/gbot/strategy/configs/config_*.json
    echo -e "${GREEN}Alte Konfigurationen geloescht.${NC}"
else
    echo -e "${GREEN}Alte Ergebnisse werden beibehalten.${NC}"
fi

# --- Eingaben ---
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH SOL): " SYMBOLS
read -p "Zeitfenster (z.B. 1h 4h): " TIMEFRAMES
read -p "Startkapital in USDT [Standard: 100]: " CAPITAL; CAPITAL=${CAPITAL:-100}
read -p "Anzahl Trials [Standard: 50]: " N_TRIALS; N_TRIALS=${N_TRIALS:-50}
read -p "Max Drawdown % [Standard: 50]: " MAX_DD; MAX_DD=${MAX_DD:-50}

# --- Pipeline starten ---
for SYMBOL in $SYMBOLS; do
    for TF in $TIMEFRAMES; do

        FULL_SYMBOL="${SYMBOL^^}/USDT:USDT"

        echo -e "\n${BLUE}=======================================================${NC}"
        echo -e "${BLUE}  Bearbeite: $FULL_SYMBOL ($TF)${NC}"
        echo -e "${BLUE}=======================================================${NC}"

        PYTHONPATH="$SCRIPT_DIR/src" python3 "$OPTIMIZER" \
            --symbol   "$FULL_SYMBOL" \
            --timeframe "$TF" \
            --capital  "$CAPITAL" \
            --trials   "$N_TRIALS" \
            --max_drawdown "$MAX_DD" \
            --settings "$SETTINGS"

        if [ $? -ne 0 ]; then
            echo -e "${RED}Fehler beim Optimieren von $FULL_SYMBOL ($TF). Ueberspringe...${NC}"
        fi

    done
done

deactivate
echo -e "\n${GREEN}======================================================="
echo "  Alle Pipeline-Aufgaben abgeschlossen!"
echo ""
echo "Naechste Schritte:"
echo "  ./run_tests.sh"
echo "  .venv/bin/python3 master_runner.py"
echo "  Cronjob: */5 * * * * .venv/bin/python3 master_runner.py"
echo -e "=======================================================${NC}"
