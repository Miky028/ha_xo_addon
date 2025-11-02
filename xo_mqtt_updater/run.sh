#!/usr/bin/env bash
set -e

# ===============================
# XO MQTT Updater run script
# ===============================

CONFIG_FILE="/data/options.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[ERROR] Konfigurační soubor neexistuje: $CONFIG_FILE"
    exit 1
fi

# Spustit Python updater s načtenou konfigurací
python3 /xo_updater.py
