#!/bin/bash
# run_tests.sh — Fuehrt alle gbot Tests aus

echo "--- Starte gbot Test-Suite ---"

if [ ! -f ".venv/bin/activate" ]; then
    echo "Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausfuehren."
    exit 1
fi

source .venv/bin/activate

export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

echo "Fuehre pytest aus..."
if python3 -m pytest -v -s; then
    echo ""
    echo "Alle Tests bestanden."
    EXIT_CODE=0
else
    PYTEST_EXIT=$?
    if [ $PYTEST_EXIT -eq 5 ]; then
        echo "Keine Tests gefunden."
        EXIT_CODE=0
    else
        echo "Tests fehlgeschlagen (Exit Code: $PYTEST_EXIT)."
        EXIT_CODE=$PYTEST_EXIT
    fi
fi

deactivate
echo "--- Test-Suite abgeschlossen ---"
exit $EXIT_CODE
