#!/bin/bash
# push_configs.sh — Config-Dateien zu GitHub pushen

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$SCRIPT_DIR"

echo -e "${YELLOW}--- gbot Config-Push ---${NC}"

# Git pruefen
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${RED}Fehler: Kein Git-Repository. Bitte zuerst 'git init' und Remote konfigurieren.${NC}"
    exit 1
fi

# Geaenderte Dateien anzeigen
echo -e "\n${YELLOW}Aktuelle Git-Status:${NC}"
git status --short

# Zu pushende Dateien
CONFIGS_DIR="src/gbot/strategy/configs"
SETTINGS="settings.json"

echo -e "\n${YELLOW}Folgende Dateien werden committed:${NC}"
echo "  - $CONFIGS_DIR/*.json"
echo "  - $SETTINGS"

read -p "Fortfahren? (j/n) [Standard: j]: " CONFIRM
CONFIRM=${CONFIRM:-j}

if [[ "$CONFIRM" != "j" && "$CONFIRM" != "J" ]]; then
    echo -e "${YELLOW}Abgebrochen.${NC}"
    exit 0
fi

# Dateien stagen
git add "$CONFIGS_DIR"/*.json 2>/dev/null || true
git add "$SETTINGS" 2>/dev/null || true

# Pruefen ob etwas zu commiten ist
if git diff --cached --quiet; then
    echo -e "${YELLOW}Keine Aenderungen zum Commiten vorhanden.${NC}"
    exit 0
fi

# Commit
TIMESTAMP=$(date +"%Y-%m-%d %H:%M")
COMMIT_MSG="update: Grid-Configs und Settings ($TIMESTAMP)"

git commit -m "$COMMIT_MSG"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}Commit erstellt.${NC}"
else
    echo -e "${RED}Commit fehlgeschlagen.${NC}"
    exit 1
fi

# Push
echo -e "\n${YELLOW}Pushe zu GitHub...${NC}"
git push

if [ $? -eq 0 ]; then
    echo -e "${GREEN}Push erfolgreich!${NC}"
else
    echo -e "${RED}Push fehlgeschlagen. Remote konfiguriert?${NC}"
    exit 1
fi

echo -e "${GREEN}--- Fertig ---${NC}"
