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
echo "  1) Grid Status Uebersicht   (alle konfigurierten und aktiven Grids)"
echo "  2) Order-Analyse            (welche Preisstufen sind aktiv?)"
echo "  3) Performance & PnL        (Gewinne, Fills, ROI aller Grids)"
echo "  4) Fibonacci-Analyse        (aktueller Range fuer alle aktiven Symbole)"
read -p "Auswahl (1-4) [Standard: 1]: " MODE

# Validierung
if [[ ! "$MODE" =~ ^[1-4]?$ ]]; then
    echo -e "${RED}Ungueltige Eingabe! Verwende Standard (1).${NC}"
    MODE=1
fi
MODE=${MODE:-1}

python3 "$RESULTS_SCRIPT" --mode "$MODE"
EXIT_CODE=$?

# --- MODUS 4: Fibonacci-Analyse ---
if [ "$MODE" == "4" ]; then
    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}Fibonacci-Analyse abgeschlossen.${NC}"
    else
        echo -e "${RED}Fehler bei der Fibonacci-Analyse.${NC}"
    fi
    deactivate
    exit $EXIT_CODE
fi

# --- MODUS 3: PnL-Zusammenfassung in settings.json eintragen? ---
if [ "$MODE" == "3" ]; then
    echo ""
    echo -e "${YELLOW}─────────────────────────────────────────────────${NC}"
    read -p "Moechtest du aktive Grids mit positivem PnL in settings.json markieren? (j/n): " AUTO_UPDATE
    AUTO_UPDATE="${AUTO_UPDATE//[$'\r\n ']/}"

    if [[ "$AUTO_UPDATE" == "j" || "$AUTO_UPDATE" == "J" || "$AUTO_UPDATE" == "y" || "$AUTO_UPDATE" == "Y" ]]; then
        echo -e "${BLUE}Aktualisiere settings.json...${NC}"

        python3 << 'EOF'
import json, glob, os

TRACKER_DIR = 'artifacts/tracker'
SETTINGS_FILE = 'settings.json'

trackers = []
for path in sorted(glob.glob(os.path.join(TRACKER_DIR, '*_grid.json'))):
    try:
        with open(path) as f:
            trackers.append(json.load(f))
    except Exception:
        pass

if not trackers:
    print("Keine Tracker-Dateien gefunden. settings.json unveraendert.")
    exit(0)

with open(SETTINGS_FILE, 'r') as f:
    settings = json.load(f)

strategies = settings.setdefault('live_trading_settings', {}).setdefault('active_strategies', [])

updated = 0
for t in trackers:
    symbol = t.get('symbol')
    pnl = t.get('performance', {}).get('realized_pnl_usdt', 0.0)
    if not symbol:
        continue
    existing = next((s for s in strategies if s.get('symbol') == symbol), None)
    if existing is None and pnl >= 0:
        strategies.append({'symbol': symbol, 'active': True})
        updated += 1
        print(f"  + {symbol} hinzugefuegt (PnL: {pnl:+.4f} USDT)")

with open(SETTINGS_FILE, 'w') as f:
    json.dump(settings, f, indent=4)

if updated:
    print(f"{updated} Strategien in settings.json eingetragen.")
else:
    print("Keine Aenderungen notwendig.")
EOF

        echo -e "${GREEN}settings.json aktualisiert.${NC}"
    else
        echo -e "${YELLOW}Keine Aenderungen an settings.json vorgenommen.${NC}"
    fi
fi

deactivate
