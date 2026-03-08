#!/bin/bash
set -e

echo "--- gbot sicheres Update ---"

echo "1. Backup von secret.json..."
cp secret.json secret.json.bak

echo "2. Neueste Version von GitHub holen..."
git fetch origin

echo "3. Lokalen Stand zuruecksetzen..."
git reset --hard origin/main

echo "4. secret.json wiederherstellen..."
cp secret.json.bak secret.json
rm secret.json.bak

echo "5. Python-Cache loeschen..."
find . -type f -name "*.pyc" -delete
find . -type d -name "__pycache__" -delete

echo "6. Ausfuehrungsrechte setzen..."
chmod +x *.sh

echo "Update abgeschlossen."
