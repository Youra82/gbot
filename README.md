# GBot

Ein vollautomatischer Grid-Trading-Bot für Krypto-Futures auf der Bitget-Börse, basierend auf dem **Grid-Trading-Prinzip** mit dynamischer **Fibonacci Retracement**-Analyse.

Dieses System wurde für den Betrieb auf einem Ubuntu-Server entwickelt und platziert automatisch Kauf- und Verkaufsorders in einem definierten Preisraster. Verlässt der Preis das Raster, berechnet der Bot den optimalen Bereich per Fibonacci neu und baut das Grid selbstständig um.

## Kernstrategie 🧱

Der Bot implementiert eine Grid-Trading-Strategie, die den Markt in gleichmäßige Preisstufen unterteilt und von Kursschwankungen profitiert.

* **Grid-Levels:** Der Preisbereich wird in `num_grids` gleiche Abstände unterteilt. An jedem Level liegt eine Limit-Order.
* **Automatische Nachfolge-Orders:**
    * Wird eine **Kauforder** gefüllt → wird automatisch eine **Verkauforder** eine Stufe höher platziert.
    * Wird eine **Verkauforder** gefüllt → wird automatisch eine **Kauforder** eine Stufe tiefer platziert.
* **Drei Grid-Modi:**
    * **neutral** – Kauf-Orders unterhalb, Verkauf-Orders oberhalb des aktuellen Preises.
    * **long** – Nur Kauf-Orders (bullisher Markt).
    * **short** – Nur Verkauf-Orders (bärischer Markt).
* **Dynamische Fibonacci-Analyse:**
    * Der Bot erkennt automatisch **Swing High und Swing Low** über einen konfigurierbaren Zeitraum.
    * Die **Fibonacci-Level** (0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%) werden berechnet.
    * Das Grid wird zwischen den günstigsten Fibonacci-Levels um den aktuellen Preis platziert.
    * Optional: Bevorzuge die **Goldene Zone (38.2%–61.8%)** für höhere Bounce-Wahrscheinlichkeit.

## Architektur & Arbeitsablauf

1. **Der Cronjob (Der Wecker):** Ein einziger Cronjob läuft in einem kurzen Intervall (z.B. alle 5 Minuten). Er startet den Master-Runner.

2. **Der Master-Runner (Der Dirigent):** Das `master_runner.py`-Skript liest alle aktiven Strategien aus der `settings.json` und startet pro Strategie einen separaten Handelsprozess.

3. **Der Handelsprozess (Der Agent):**
    * `run.py` wird für eine spezifische Strategie gestartet.
    * Der **Guardian-Decorator** fängt alle Fehler ab und sendet Telegram-Alarme.
    * Die Kernlogik in `trade_manager.py` wird ausgeführt:
        1. Beim ersten Start: Fibonacci-Analyse → Grid-Bereich berechnen → Orders platzieren.
        2. Jeder Folge-Zyklus: Rebalancing prüfen (Preis außerhalb Bereich?) → ggf. alle Orders stornieren, Fibonacci neu berechnen, Grid neu aufbauen.
        3. Fills erkennen → Nachfolge-Orders automatisch platzieren.
    * Der **Tracker** (`artifacts/tracker/<symbol>_grid.json`) speichert alle aktiven Orders und Performance-Daten persistent.

---

## Installation 🚀

Führe die folgenden Schritte auf einem frischen Ubuntu-Server (oder lokal) aus.

#### 1. Projekt klonen

```bash
git clone https://github.com/Youra82/gbot.git
```

#### 2. Installations-Skript ausführen

```bash
cd gbot
chmod +x install.sh
bash ./install.sh
```

#### 3. API-Schlüssel eintragen

Erstelle die `secret.json` und trage deine Bitget-API-Schlüssel ein:

```bash
nano secret.json
```

```json
{
    "gbot": {
        "api_key": "DEIN_API_KEY",
        "secret": "DEIN_SECRET",
        "passphrase": "DEIN_PASSPHRASE",
        "telegram_bot_token": "DEIN_BOT_TOKEN",
        "telegram_chat_id": "DEINE_CHAT_ID"
    }
}
```

Speichere mit `Strg + X`, dann `Y`, dann `Enter`.

---

## Konfiguration & Automatisierung

#### 1. Strategie konfigurieren (Pipeline)

Führe die interaktive Pipeline aus, um eine neue Grid-Strategie einzurichten. Fibonacci wird automatisch berechnet — keine manuellen Preisgrenzen nötig.

Skripte aktivieren (einmalig):

```bash
chmod +x *.sh
```

Pipeline starten:

```bash
./run_pipeline.sh
```

Die Pipeline fragt nach:
- Symbol (z.B. `BTC/USDT:USDT`)
- Grid-Modus (`neutral` / `long` / `short`)
- Anzahl der Grid-Stufen
- Kapital, Hebel, Margin-Modus
- Fibonacci automatisch oder manuellen Preisbereich

Danach rechnet Fibonacci automatisch und zeigt eine Vorschau mit Grid-Abstand und ROI-Schätzung.

#### 2. Ergebnisse analysieren

```bash
./show_results.sh
```

* **Modus 1:** Grid-Status aller aktiven Strategien (Bereich, Orders, Fibonacci-Meta).
* **Modus 2:** Order-Analyse nach Preis-Level.
* **Modus 3:** PnL-Performance (ROI, Fills, Gebühren).
* **Modus 4:** Vollständige Code-Dokumentation.

#### 3. Strategie aktivieren

Bearbeite die `settings.json` und trage die gewünschte Strategie ein:

```bash
nano settings.json
```

```json
{
    "active_strategies": [
        "config_BTC_USDT_USDT"
    ]
}
```

#### 4. Automatisierung per Cronjob einrichten

```bash
crontab -e
```

Füge folgende Zeile ein (Pfad anpassen):

```bash
# Starte den GBot Master-Runner alle 5 Minuten
*/5 * * * * /usr/bin/flock -n /root/gbot/gbot.lock /bin/sh -c "cd /root/gbot && .venv/bin/python3 master_runner.py >> /root/gbot/logs/cron.log 2>&1"
```

Logverzeichnis anlegen:

```bash
mkdir -p /root/gbot/logs
```

---

## Tägliche Verwaltung & Wichtige Befehle ⚙️

#### Status ansehen

```bash
./show_status.sh
```

#### Logs ansehen

* **Logs live mitverfolgen:**
    ```bash
    tail -f logs/cron.log
    ```
* **Nach Fehlern suchen:**
    ```bash
    grep -i "ERROR" logs/cron.log
    ```
* **Individuelle Strategie-Logs:**
    ```bash
    tail -n 100 logs/gbot_BTCUSDTUSDT_4h.log
    ```

#### Manueller Start (Test)

```bash
cd /root/gbot && .venv/bin/python3 master_runner.py
```

#### Bot aktualisieren

```bash
./update.sh
```

#### Grid zurücksetzen (Rebalancing erzwingen)

```bash
rm artifacts/tracker/BTCUSDTUSDTUSDT_grid.json
```

Beim nächsten Zyklus wird das Grid neu initialisiert inkl. frischer Fibonacci-Analyse.

#### Fibonacci-Analyse manuell ausführen

```bash
.venv/bin/python3 src/gbot/analysis/fibonacci.py --symbol BTC/USDT:USDT --timeframe 4h --lookback 200
```

Mit JSON-Ausgabe (für Shell-Scripting):

```bash
.venv/bin/python3 src/gbot/analysis/fibonacci.py --symbol BTC/USDT:USDT --timeframe 4h --lookback 200 --json
```

---

## Qualitätssicherung & Tests 🛡️

**Wann ausführen?** Nach jedem Update oder Code-Änderungen.

```bash
./run_tests.sh
```

* **Erfolgreich:** Alle Tests `PASSED` (Grün).
* **Fehler:** Tests `FAILED` (Rot). Der Bot sollte nicht live gehen.

---

## Git Management

Konfiguration pushen:

```bash
./push_configs.sh
```

Projektstatus prüfen:

```bash
./show_status.sh
```

Manuelles Backup:

```bash
git add .
git commit -m "Update gbot Konfiguration"
git push origin main
```

---

### ⚠️ Disclaimer

Dieses Material dient ausschließlich zu Bildungs- und Unterhaltungszwecken. Es handelt sich nicht um eine Finanzberatung. Der Nutzer trägt die alleinige Verantwortung für alle Handlungen. Der Autor haftet nicht für etwaige Verluste. Trading mit Krypto-Futures beinhaltet ein hohes Risiko.
