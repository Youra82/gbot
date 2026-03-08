#!/bin/bash
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}======================================================="
echo "           gbot Installations-Skript (Grid Trading)"
echo "=======================================================${NC}"

echo -e "\n${YELLOW}1/4: System-Abhaengigkeiten installieren...${NC}"
sudo apt-get update
sudo apt-get install -y python3 python3-venv git curl
echo -e "${GREEN}Fertig.${NC}"

echo -e "\n${YELLOW}2/4: Python-Umgebung erstellen (.venv)...${NC}"
INSTALL_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$INSTALL_DIR"

if [ -d ".venv" ]; then
    echo "Entferne alte .venv..."
    rm -rf .venv
fi

python3 -m venv .venv --upgrade-deps
echo -e "${GREEN}Fertig.${NC}"

echo -e "\n${YELLOW}3/4: Python-Bibliotheken installieren...${NC}"
VENV_PIP=".venv/bin/pip"
$VENV_PIP install --upgrade pip setuptools wheel

if [ -f "requirements.txt" ]; then
    $VENV_PIP install -r requirements.txt
    echo -e "${GREEN}Fertig.${NC}"
else
    echo -e "${YELLOW}WARNUNG: requirements.txt nicht gefunden.${NC}"
fi

echo -e "\n${YELLOW}4/4: Ausfuehrungsrechte setzen...${NC}"
chmod +x *.sh
echo -e "${GREEN}Fertig.${NC}"

echo -e "\n${GREEN}======================================================="
echo "  Installation abgeschlossen!"
echo ""
echo "Naechste Schritte:"
echo "  1. secret.json anlegen mit API-Keys:"
echo '     { "gbot": [{"name": "main", "apiKey": "...", "secret": "...", "password": "..."}], "telegram": {"bot_token": "...", "chat_id": "..."} }'
echo "  2. Konfiguration anpassen:"
echo "     nano src/gbot/strategy/configs/config_BTC_USDT_USDT.json"
echo "  3. Aktive Strategien in settings.json eintragen."
echo "  4. Bot starten:"
echo "     .venv/bin/python3 master_runner.py"
echo "=======================================================${NC}"
