#!/usr/bin/env python3
import os
import json
import time
import requests
import paho.mqtt.client as mqtt
from datetime import datetime

# ========================
# Funkce logování
# ========================
def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)

def debug(msg):
    if DEBUG:
        log(f"[DEBUG] {msg}")

# ========================
# Načtení konfigurace
# ========================
CONFIG_FILE = "/data/options.json"
with open(CONFIG_FILE, "r") as f:
    cfg = json.load(f)

XO_URL = cfg.get("xo_url", "https://xo.local")
XO_TOKEN = cfg.get("xo_token", "")
HOST_UUID = cfg.get("host_uuid", "")
HOST_NAME = cfg.get("host_name", "")
MQTT_HOST = cfg.get("mqtt_host", "core-mosquitto")
MQTT_PORT = int(cfg.get("mqtt_port", 1883))
MQTT_USER = cfg.get("mqtt_user", "")
MQTT_PASSWORD = cfg.get("mqtt_password", "")
MQTT_TOPIC = cfg.get("mqtt_topic", "xcp-ng/host")
UPDATE_INTERVAL = int(cfg.get("update_interval", 30))
VERIFY_SSL = bool(cfg.get("verify_ssl", False))
DEBUG = bool(cfg.get("debug", False))
NETWORK_INTERFACE = cfg.get("network_interface", "2")  # vybraná síťovka

log("Načtená konfigurace:")
log(f"  XO_URL       = {XO_URL}")
log(f"  HOST_UUID    = {HOST_UUID}")
log(f"  HOST_NAME    = {HOST_NAME}")
log(f"  MQTT_HOST    = {MQTT_HOST}:{MQTT_PORT}")
log(f"  MQTT_TOPIC   = {MQTT_TOPIC}")
log(f"  UPDATE_INTERVAL = {UPDATE_INTERVAL}s")
log(f"  VERIFY_SSL   = {VERIFY_SSL}")
log(f"  DEBUG        = {DEBUG}")
log(f"  NETWORK_INTERFACE = {NETWORK_INTERFACE}")

# ========================
# Funkce pro čtení statistik hosta
# ========================
def fetch_host_stats(xo_url, host_uuid, token, verify_ssl=True):
    debug(f"Volání fetch_host_stats(xo_url={xo_url}, host_uuid={host_uuid}, token=****, verify_ssl={verify_ssl})")
    headers = {"Cookie": f"authenticationToken={token}"}
    url = f"{xo_url}/rest/v0/hosts/{host_uuid}/stats"
    debug(f"Volám URL: {url}")

    try:
        r = requests.get(url, headers=headers, timeout=10, verify=verify_ssl)
        r.raise_for_status()
        stats = r.json().get("stats", {})

        debug(f"Data z XO API (oříznuto): {json.dumps(stats)[:300]}...")
        if not stats:
            log("XO API nevrátilo žádná data.", "WARNING")
            return {}

        # CPU load - použít jen poslední hodnotu load
        cpu_load = stats.get("load", [])
        cpu = cpu_load[-1] if cpu_load else 0

        # Memory
        mem_total = stats.get("memory", [0])[-1] if stats.get("memory") else 0
        mem_free = stats.get("memoryFree", [0])[-1] if stats.get("memoryFree") else 0
        mem_used_pct = ((mem_total - mem_free) / mem_total * 100) if mem_total else 0

        # Disk write
        disk_write = sum(val.get("io_write", [0])[-1] for k,val in stats.items() if isinstance(val, dict) and "io_write" in val)

        # Network TX/RX - vybraná interface
        pifs = stats.get("pifs", {})
        net_rx = pifs.get("rx", {}).get(NETWORK_INTERFACE, [0])[-1] if "rx" in pifs else 0
        net_tx = pifs.get("tx", {}).get(NETWORK_INTERFACE, [0])[-1] if "tx" in pifs else 0
        net_tx_mbps = net_tx * 8 / 1_000_000
        net_rx_mbps = net_rx * 8 / 1_000_000

        metrics = {
            "timestamp": datetime.utcnow().isoformat(),
            "host_uuid": host_uuid,
            "host_name": HOST_NAME,
            "cpu_load": round(cpu,2),
            "memory_used_pct": round(mem_used_pct,2),
            "memory_total_gb": round(mem_total/(1024**3),2),
            "memory_free_gb": round(mem_free/(1024**3),2),
            "disk_write_gb": round(disk_write/1_000_000_000,2),
            "network_tx_mbps": round(net_tx_mbps,2),
            "network_rx_mbps": round(net_rx_mbps,2)
        }
        debug(f"Parsované metriky: {metrics}")
        return metrics

    except requests.exceptions.RequestException as e:
        log(f"Chyba při připojení k XO API: {e}", "ERROR")
        return {}

# ========================
# MQTT publikace
# ========================
def publish_metrics(client, topic, metrics):
    try:
        client.publish(topic, json.dumps(metrics))
        debug(f"MQTT publikováno na {topic}: {metrics}")
    except Exception as e:
        log(f"Chyba při publikování do MQTT: {e}", "ERROR")

# ========================
# Hlavní smyčka
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
        metrics = fetch_host_stats(XO_URL, HOST_UUID, XO_TOKEN, VERIFY_SSL)
        if metrics:
            publish_metrics(client, MQTT_TOPIC, metrics)
        else:
            log(f"Žádné metriky k publikování pro hosta {HOST_UUID}", "WARNING")
        time.sleep(UPDATE_INTERVAL)

# ========================
# Spuštění
# ========================
if __name__ == "__main__":
    log("Spouštím XO MQTT Updater v1.2.3 ...")
    main()
