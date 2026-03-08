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
echo -e "${GREEN}✔ Virtuelle Umgebung wurde erfolgreich aktiviert.${NC}"

# --- Aufraeum-Assistent ---
echo -e "\n${YELLOW}Moechtest du alle alten Configs vor dem Start loeschen?${NC}"
read -p "Empfohlen fuer kompletten Neustart. (j/n) [Standard: n]: " CLEANUP
CLEANUP=${CLEANUP:-n}
if [[ "$CLEANUP" == "j" || "$CLEANUP" == "J" ]]; then
    rm -f src/gbot/strategy/configs/config_*.json
    echo -e "${GREEN}✔ Alte Konfigurationen geloescht.${NC}"
else
    echo -e "${GREEN}✔ Alte Ergebnisse werden beibehalten.${NC}"
fi

# --- Symbole & Zeitfenster ---
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH SOL): " SYMBOLS
read -p "Zeitfenster eingeben (z.B. 1h 4h): " TIMEFRAMES

# --- Rückblick-Empfehlung & Datum ---
echo -e "\n${BLUE}--- Empfehlung: Optimaler Rueckblick-Zeitraum ---${NC}"
printf "+-------------+--------------------------------+\n"
printf "| Zeitfenster | Empfohlener Rueckblick (Tage)  |\n"
printf "+-------------+--------------------------------+\n"
printf "| 5m, 15m     | 15 - 90 Tage                   |\n"
printf "| 30m, 1h     | 180 - 365 Tage                 |\n"
printf "| 2h, 4h      | 550 - 730 Tage                 |\n"
printf "| 6h, 1d      | 1095 - 1825 Tage               |\n"
printf "+-------------+--------------------------------+\n"

read -p "Startdatum (JJJJ-MM-TT) oder 'a' fuer Automatik [Standard: a]: " START_DATE_INPUT
START_DATE_INPUT=${START_DATE_INPUT:-a}
read -p "Enddatum (JJJJ-MM-TT) [Standard: Heute]: " END_DATE
END_DATE=${END_DATE:-$(date +%F)}

# --- Weitere Parameter ---
read -p "Startkapital in USDT [Standard: 100]: " CAPITAL; CAPITAL=${CAPITAL:-100}
read -p "CPU-Kerne [Standard: -1 fuer alle]: " N_CORES; N_CORES=${N_CORES:--1}
read -p "Anzahl Trials [Standard: 50]: " N_TRIALS; N_TRIALS=${N_TRIALS:-50}

# --- Optimierungs-Modus ---
echo -e "\n${YELLOW}Waehle einen Optimierungs-Modus:${NC}"
echo "  1) Strenger Modus  (Max ROI mit Drawdown-Limit)"
echo "  2) 'Finde das Beste'-Modus  (Max ROI, kein Limit)"
read -p "Auswahl (1-2) [Standard: 1]: " OPTIM_MODE; OPTIM_MODE=${OPTIM_MODE:-1}

if [ "$OPTIM_MODE" == "1" ]; then
    OPTIM_MODE_ARG="strict"
else
    OPTIM_MODE_ARG="best_profit"
fi
read -p "Max Drawdown % [Standard: 30]: " MAX_DD; MAX_DD=${MAX_DD:-30}

# --- Pipeline starten ---
for SYMBOL in $SYMBOLS; do
    for TF in $TIMEFRAMES; do

        FULL_SYMBOL="${SYMBOL^^}/USDT:USDT"

        # --- Startdatum berechnen (Automatik) ---
        if [ "$START_DATE_INPUT" == "a" ]; then
            LOOKBACK_DAYS=365
            case "$TF" in
                5m|15m)  LOOKBACK_DAYS=60   ;;
                30m|1h)  LOOKBACK_DAYS=365  ;;
                2h|4h)   LOOKBACK_DAYS=730  ;;
                6h|1d)   LOOKBACK_DAYS=1095 ;;
            esac
            FINAL_START_DATE=$(date -d "$LOOKBACK_DAYS days ago" +%F)
            echo -e "${YELLOW}INFO: Automatisches Startdatum fuer $TF (${LOOKBACK_DAYS} Tage): $FINAL_START_DATE${NC}"
        else
            FINAL_START_DATE=$START_DATE_INPUT
        fi

        echo -e "\n${BLUE}=======================================================${NC}"
        echo -e "${BLUE}  Bearbeite: $FULL_SYMBOL ($TF)${NC}"
        echo -e "${BLUE}  Zeitraum : $FINAL_START_DATE bis $END_DATE${NC}"
        echo -e "${BLUE}=======================================================${NC}"

        PYTHONPATH="$SCRIPT_DIR/src" python3 "$OPTIMIZER" \
            --symbol       "$FULL_SYMBOL" \
            --timeframe    "$TF" \
            --capital      "$CAPITAL" \
            --trials       "$N_TRIALS" \
            --max_drawdown "$MAX_DD" \
            --start_date   "$FINAL_START_DATE" \
            --end_date     "$END_DATE" \
            --jobs         "$N_CORES" \
            --mode         "$OPTIM_MODE_ARG" \
            --settings     "$SETTINGS"

        if [ $? -ne 0 ]; then
            echo -e "${RED}Fehler beim Optimieren von $FULL_SYMBOL ($TF). Ueberspringe...${NC}"
        fi

    done
done

deactivate
echo -e "\n${GREEN}======================================================="
echo "  ✔ Alle Pipeline-Aufgaben erfolgreich abgeschlossen!"
echo ""
echo "Naechste Schritte:"
echo "  ./run_tests.sh"
echo "  .venv/bin/python3 master_runner.py"
echo "  Cronjob: */5 * * * * .venv/bin/python3 master_runner.py"
echo -e "=======================================================${NC}"
