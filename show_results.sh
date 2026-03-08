#!/bin/bash
# show_results.sh — Grid Analyse-Tool (4 Modi)

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$SCRIPT_DIR"

VENV_PATH=".venv"
VENV_ACTIVATE="$VENV_PATH/bin/activate"
VENV_PYTHON="$VENV_PATH/bin/python3"
RESULTS_SCRIPT="src/gbot/analysis/show_results.py"

# venv pruefen
if [ ! -f "$VENV_PYTHON" ]; then
    echo -e "${RED}Virtuelle Umgebung nicht gefunden. Bitte install.sh ausfuehren.${NC}"
    exit 1
fi

source "$VENV_ACTIVATE"

# Abhaengigkeiten pruefen (nur pandas und requests noetig)
if ! "$VENV_PYTHON" -c "import pandas, requests" 2>/dev/null; then
    echo -e "${YELLOW}Installiere fehlende Abhaengigkeiten...${NC}"
    .venv/bin/pip install -r requirements.txt --quiet 2>/dev/null || true
fi

# --- Modus-Menue ---
echo -e "\n${YELLOW}Waehle einen Analyse-Modus fuer gbot:${NC}"
echo "  1) Grid Status Uebersicht (alle konfigurierten und aktiven Grids)"
echo "  2) Order-Analyse nach Grid-Levels (welche Preisstufen aktiv?)"
echo "  3) Performance & PnL Analyse (Gewinne, Fills, ROI)"
echo "  4) Vollstaendige Code-Dokumentation (alle Quellcode-Dateien)"
read -p "Auswahl (1-4) [Standard: 1]: " MODE
MODE=${MODE:-1}

# Python-Analyse starten
"$VENV_PYTHON" "$RESULTS_SCRIPT" --mode "$MODE"

if command -v deactivate &> /dev/null; then
    deactivate
fi
