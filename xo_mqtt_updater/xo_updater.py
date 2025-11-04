#!/usr/bin/env python3
import os
import json
import time
import requests
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta

# ========================
# KÓDEM DEFINOVANÁ VERZE
# ========================
VERSION = "1.2.21"

# ========================
# Globální konstanty
# ========================
PUBLISH_INTERVAL_S = 5  # Publikujeme každých 5 sekund

# ========================
# Funkce logování
# ========================
def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)

def debug(msg):
    if 'DEBUG' in globals() and DEBUG:
        log(msg, level="DEBUG")

# ========================
# Načtení konfigurace
# ========================
CONFIG_FILE = "/data/options.json"
try:
    with open(CONFIG_FILE, "r") as f:
        cfg = json.load(f)
except FileNotFoundError:
    log(f"Chyba: Konfigurační soubor {CONFIG_FILE} nenalezen.", "CRITICAL")
    cfg = {}

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
NETWORK_INTERFACE = cfg.get("network_interface", "2")

if UPDATE_INTERVAL % PUBLISH_INTERVAL_S != 0:
    log(f"Chyba: UPDATE_INTERVAL ({UPDATE_INTERVAL}s) musí být násobkem PUBLISH_INTERVAL_S ({PUBLISH_INTERVAL_S}s). Použiji 30s.", "ERROR")
    UPDATE_INTERVAL = 30
NUM_SAMPLES = UPDATE_INTERVAL // PUBLISH_INTERVAL_S

# ========================
# MQTT Callback funkce
# ========================
def on_connect(client, userdata, flags, rc):
    """Zpracovává výsledek připojení k brokeru."""
    if rc == 0:
        log("MQTT: Připojení k brokeru ÚSPĚŠNÉ. (Kód: 0)", "INFO")
        time.sleep(1)  # zajistí, že session je aktivní před publikací
        publish_discovery_config(client)
    elif rc == 5:
        log("MQTT: Připojení SELHALO - Chyba autentizace/autorizace. (Kód: 5)", "CRITICAL")
    else:
        log(f"MQTT: Připojení SELHALO. (Kód: {rc})", "CRITICAL")

def on_disconnect(client, userdata, rc):
    log(f"MQTT: Klient odpojen s kódem {rc}.", "WARNING")

def on_publish(client, userdata, mid):
    debug(f"MQTT: Zpráva (ID: {mid}) úspěšně doručena brokeru.")

# ========================
# FUNKCE PRO MQTT DISCOVERY
# ========================
def publish_discovery_config(client):
    """Publikuje konfigurační payload pro každý senzor (MQTT Discovery pro Home Assistant)."""
    global VERSION

    if not HOST_UUID or not HOST_NAME:
        log("Chyba: Chybí HOST_UUID nebo HOST_NAME pro MQTT Discovery.", "ERROR")
        return

    device_info = {
        "identifiers": [f"xcp_ng_{HOST_UUID}"],
        "name": HOST_NAME,
        "model": "XCP-NG Host",
        "manufacturer": "Xen Orchestra",
        "sw_version": VERSION
    }

    metric_configs = {
        "cpu_total_load": ["CPU Load", "%", "mdi:chip", "measurement"],
        "memory_used_pct": ["Memory Used", "%", "mdi:memory", "measurement"],
        "network_tx_kbps": ["Network TX", "kbps", "mdi:upload-network", "data_rate"],
        "network_rx_kbps": ["Network RX", "kbps", "mdi:download-network", "data_rate"],
    }

    STATE_TOPIC = f"{MQTT_TOPIC}/state"

    log("Publikuji konfigurační zprávy pro MQTT Discovery...")

    for key, (name, unit, icon, device_class) in metric_configs.items():
        discovery_topic = f"homeassistant/sensor/xcp_ng/{key}/config"

        payload = {
            "name": f"{HOST_NAME} {name}",
            "unique_id": f"{HOST_UUID}_{key}",
            "state_topic": STATE_TOPIC,
            "unit_of_measurement": unit,
            "icon": icon,
            "device_class": device_class,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "force_update": True,
            "device": device_info
        }

        payload_bytes = json.dumps(payload).encode('utf-8')
        result = client.publish(discovery_topic, payload_bytes, qos=1, retain=True)
        if result.rc == 0:
            log(f"Discovery publikováno pro {key} (retained).", "INFO")
        else:
            log(f"Chyba při publikaci discovery pro {key} (RC={result.rc})", "ERROR")

    log("Discovery konfigurace úspěšně publikována (retained).")

# ========================
# Funkce pro čtení statistik
# ========================
def fetch_host_stats(xo_url, host_uuid, token, verify_ssl=True):
    debug(f"Volání fetch_host_stats(xo_url={xo_url}, host_uuid={host_uuid})")
    headers = {"Cookie": f"authenticationToken={token}"}
    url = f"{xo_url.rstrip('/')}/rest/v0/hosts/{host_uuid}/stats"

    try:
        r = requests.get(url, headers=headers, timeout=10, verify=verify_ssl)
        r.raise_for_status()
        full_response = r.json()
        stats = full_response.get("stats", {})
        xo_interval = full_response.get("interval", 5)
        end_timestamp = full_response.get("endTimestamp", int(time.time()))

        if not stats:
            log("XO API nevrátilo žádná data.", "WARNING")
            return {}

        # CPU
        aggregated_cpu_series = [0.0] * NUM_SAMPLES
        cpu_metrics_dict = stats.get("cpus", {})
        num_cpu_cores = len(cpu_metrics_dict)
        for cpu_data in cpu_metrics_dict.values():
            samples = cpu_data[-NUM_SAMPLES:]
            samples += [0.0] * (NUM_SAMPLES - len(samples))
            for i in range(NUM_SAMPLES):
                aggregated_cpu_series[i] += samples[i]
        if num_cpu_cores > 0:
            aggregated_cpu_series = [s / num_cpu_cores for s in aggregated_cpu_series]

        # Memory
        mem_total_series = stats.get("memory", [0])[-NUM_SAMPLES:]
        mem_free_series = stats.get("memoryFree", [0])[-NUM_SAMPLES:]
        mem_used_pct_series = [
            round(((t - f) / t * 100) if t else 0, 2) for t, f in zip(mem_total_series, mem_free_series)
        ]
        mem_used_pct_series += [0.0] * (NUM_SAMPLES - len(mem_used_pct_series))

        # Síť
        pifs_metrics = stats.get("pifs", {})
        tx_metrics = pifs_metrics.get("tx", {}).get(str(NETWORK_INTERFACE), [])
        rx_metrics = pifs_metrics.get("rx", {}).get(str(NETWORK_INTERFACE), [])
        net_tx_kbps_series = [round(v * 8 / 1000, 2) for v in tx_metrics[-NUM_SAMPLES:]]
        net_rx_kbps_series = [round(v * 8 / 1000, 2) for v in rx_metrics[-NUM_SAMPLES:]]
        net_tx_kbps_series += [0.0] * (NUM_SAMPLES - len(net_tx_kbps_series))
        net_rx_kbps_series += [0.0] * (NUM_SAMPLES - len(net_rx_kbps_series))

        return {
            "cpu_total_load": [round(v, 2) for v in aggregated_cpu_series],
            "memory_used_pct": mem_used_pct_series,
            "network_tx_kbps": net_tx_kbps_series,
            "network_rx_kbps": net_rx_kbps_series,
            "end_timestamp": end_timestamp,
            "xo_interval": xo_interval
        }

    except Exception as e:
        log(f"Chyba při fetch_host_stats: {e}", "ERROR")
        return {}

# ========================
# Publikace jednoho vzorku
# ========================
def publish_current_sample(client, topic, buffer, index):
    try:
        state_topic = f"{topic}/state"
        json_payload = {
            "uid": HOST_UUID,
            "cpu_total_load": f"{buffer['cpu_total_load'][index]:.2f}",
            "memory_used_pct": f"{buffer['memory_used_pct'][index]:.2f}",
            "network_tx_kbps": f"{buffer['network_tx_kbps'][index]:.2f}",
            "network_rx_kbps": f"{buffer['network_rx_kbps'][index]:.2f}"
        }
        client.publish(state_topic, json.dumps(json_payload), qos=1, retain=False)
        debug(f"Publikováno na {state_topic}: {json_payload}")
    except Exception as e:
        log(f"Chyba při publikaci do MQTT: {e}", "ERROR")

# ========================
# Hlavní smyčka
# ========================
def main():
    client_id = f"xcp-ng-exporter"
    log(f"MQTT Client ID: {client_id}")
    client = mqtt.Client(client_id=client_id)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        time.sleep(2)
    except Exception as e:
        log(f"Chyba při připojení k MQTT brokeru: {e}", "CRITICAL")
        return

    metrics_buffer = {}
    sample_index = NUM_SAMPLES
    last_fetch_time = 0

    while True:
        now = time.time()
        if sample_index >= NUM_SAMPLES or now - last_fetch_time >= UPDATE_INTERVAL:
            log("Stahuji nová data z XO API...")
            new_buffer = fetch_host_stats(XO_URL, HOST_UUID, XO_TOKEN, VERIFY_SSL)
            if new_buffer:
                metrics_buffer = new_buffer
                sample_index = 0
                last_fetch_time = now
            else:
                log("Nepodařilo se stáhnout data, opakuji za 5s.", "ERROR")
                time.sleep(PUBLISH_INTERVAL_S)
                continue

        if metrics_buffer and sample_index < NUM_SAMPLES:
            publish_current_sample(client, MQTT_TOPIC, metrics_buffer, sample_index)
            sample_index += 1

        time.sleep(PUBLISH_INTERVAL_S)

if __name__ == "__main__":
    log(f"Spouštím XO MQTT Updater v{VERSION}")
    main()
