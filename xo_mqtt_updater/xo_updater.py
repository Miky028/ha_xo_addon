#!/usr/bin/env python3
import os
import json
import time
import requests
import paho.mqtt.client as mqtt
from datetime import datetime

# ========================
# Pomocné funkce logování
# ========================

def log(message, level="INFO"):
    print(f"[{level}] {message}", flush=True)

def debug_log(message):
    if DEBUG:
        log(f"[DEBUG] {message}")

# ========================
# Načtení konfigurace
# ========================

CONFIG_PATH = "/data/options.json"
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
else:
    config = {}

# Načtení hodnot s výchozími hodnotami
XO_URL = config.get("xo_url", "https://xo.local")
XO_TOKEN = config.get("xo_token", "")
HOST_UUID = config.get("host_uuid", "")
MQTT_HOST = config.get("mqtt_host", "core-mosquitto")
MQTT_PORT = int(config.get("mqtt_port", 1883))
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASSWORD = config.get("mqtt_password", "")
MQTT_TOPIC = config.get("mqtt_topic", "xcp-ng/host")
INTERVAL = int(config.get("interval", 30))
VERIFY_SSL = bool(config.get("verify_ssl", False))
DEBUG = bool(config.get("debug", False))

log("Načtená konfigurace:")
log(f"  XO_URL       = {XO_URL}")
log(f"  HOST_UUID    = {HOST_UUID}")
log(f"  MQTT_HOST    = {MQTT_HOST}:{MQTT_PORT}")
log(f"  MQTT_TOPIC   = {MQTT_TOPIC}")
log(f"  INTERVAL     = {INTERVAL}s")
log(f"  VERIFY_SSL   = {VERIFY_SSL}")
log(f"  DEBUG        = {DEBUG}")

# ========================
# Funkce pro čtení dat z XO API
# ========================

def fetch_host_stats(xo_url, host_uuid, token, verify_ssl=True):
    debug_log(f"Volání fetch_host_stats(xo_url={xo_url}, host_uuid={host_uuid}, token=****, verify_ssl={verify_ssl})")
    headers = {"Cookie": f"authenticationToken={token}"}
    url = f"{xo_url}/rest/v0/hosts/{host_uuid}/stats"
    debug_log(f"Volám URL: {url}")

    try:
        r = requests.get(url, headers=headers, timeout=10, verify=verify_ssl)
        if r.status_code != 200:
            log(f"Chyba XO API {r.status_code}: {r.text}", "WARNING")
            return {}

        data = r.json()
        stats = data.get("stats", {})
        if not stats:
            log("XO API nevrátilo žádná data v klíči 'stats'", "WARNING")
            return {}

        debug_log(f"Data z XO API (oříznuto): {json.dumps(stats)[:300]}...")

        # CPU usage – průměr poslední hodnoty všech CPU jader
        cpus = stats.get("cpus", {})
        if isinstance(cpus, dict) and cpus:
            cpu_values = [values[-1] for values in cpus.values() if values]
            cpu_avg = sum(cpu_values) / len(cpu_values) if cpu_values else 0
        else:
            cpu_avg = 0

        # Memory usage – využití RAM v %
        mem_total = stats.get("memory", [0])[-1] if stats.get("memory") else 0
        mem_free = stats.get("memoryFree", [0])[-1] if stats.get("memoryFree") else 0
        mem_used_pct = ((mem_total - mem_free) / mem_total * 100) if mem_total > 0 else 0

        # Disk activity – IO zápisy (v bajtech) sečtené přes zařízení
        disk_usage = 0
        for key, val in stats.items():
            if key.startswith("xvd") or key.startswith("sd"):
                if isinstance(val, dict) and "io_write" in val and val["io_write"]:
                    disk_usage += val["io_write"][-1]

        # Síťová aktivita – přenosy všech pif_* rozhraní
        net_tx = 0
        net_rx = 0
        for key, val in stats.items():
            if key.startswith("pif_") and isinstance(val, dict):
                if "tx" in val and val["tx"]:
                    net_tx += val["tx"][-1]
                if "rx" in val and val["rx"]:
                    net_rx += val["rx"][-1]

        net_tx_mbps = net_tx * 8 / 1_000_000
        net_rx_mbps = net_rx * 8 / 1_000_000

        metrics = {
            "timestamp": datetime.utcnow().isoformat(),
            "cpu": round(cpu_avg, 2),
            "memory_used_pct": round(mem_used_pct, 2),
            "memory_total_gb": round(mem_total / (1024**3), 2),
            "memory_free_gb": round(mem_free / (1024**3), 2),
            "disk_write_gb": round(disk_usage / 1_000_000_000, 2),
            "network_tx_mbps": round(net_tx_mbps, 2),
            "network_rx_mbps": round(net_rx_mbps, 2)
        }

        debug_log(f"Parsované metriky: {metrics}")
        return metrics

    except requests.exceptions.RequestException as e:
        log(f"Chyba při připojení k XO API: {e}", "ERROR")
        return {}

# ========================
# MQTT publikování
# ========================

def publish_metrics(client, topic, metrics):
    try:
        payload = json.dumps(metrics)
        client.publish(topic, payload)
        debug_log(f"MQTT publikováno na {topic}: {payload}")
    except Exception as e:
        log(f"Chyba při publikování do MQTT: {e}", "ERROR")

# ========================
# Hlavní funkce
# ========================

def main():
    log("Inicializuji MQTT klienta...")
    client = mqtt.Client()
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
    except Exception as e:
        log(f"Chyba při připojení k MQTT brokeru: {e}", "ERROR")
        return

    log("MQTT klient připojen. Zahajuji smyčku...")

    while True:
        metrics = fetch_host_stats(XO_URL, HOST_UUID, XO_TOKEN, verify_ssl=VERIFY_SSL)
        if metrics:
            publish_metrics(client, MQTT_TOPIC, metrics)
        else:
            log(f"Žádné metriky k publikování pro hosta {HOST_UUID}", "WARNING")

        time.sleep(INTERVAL)

# ========================
# Spuštění
# ========================

if __name__ == "__main__":
    log("Spouštím XO MQTT updater v1.2.2 ...")
    main()
