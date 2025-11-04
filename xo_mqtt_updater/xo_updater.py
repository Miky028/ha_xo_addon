#!/usr/bin/env python3
import os
import json
import time
import requests
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import Sensor

# ========================
# KÓDEM DEFINOVANÁ VERZE
# ========================
VERSION = "1.2.26"

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

PUBLISH_INTERVAL_S = 5
if UPDATE_INTERVAL % PUBLISH_INTERVAL_S != 0:
    log(f"Chyba: UPDATE_INTERVAL ({UPDATE_INTERVAL}s) musí být násobkem PUBLISH_INTERVAL_S ({PUBLISH_INTERVAL_S}s). Použiji 30s.", "ERROR")
    UPDATE_INTERVAL = 30
NUM_SAMPLES = UPDATE_INTERVAL // PUBLISH_INTERVAL_S

# ========================
# Nastavení HA MQTT Discoverable
# ========================
device_info = DeviceInfo(
    identifiers=[HOST_UUID],
    name=HOST_NAME,
    manufacturer="Xen Orchestra",
    model="XCP-NG Host",
    sw_version=VERSION
)

mqtt_settings = Settings.MQTT(
    host=MQTT_HOST,
    port=MQTT_PORT,
    username=MQTT_USER,
    password=MQTT_PASSWORD,
    discovery_prefix="homeassistant",
)

# Definice senzorů
cpu_sensor = Sensor(
    name=f"{HOST_NAME} CPU Load",
    unique_id=f"{HOST_UUID}_cpu_total_load",
    unit_of_measurement="%",
    device_class="power_factor",
    state_class="measurement",
    icon="mdi:chip",
    device=device_info,
    mqtt=mqtt_settings
)

memory_sensor = Sensor(
    name=f"{HOST_NAME} Memory Used",
    unique_id=f"{HOST_UUID}_memory_used_pct",
    unit_of_measurement="%",
    device_class="memory",
    state_class="measurement",
    icon="mdi:memory",
    device=device_info,
    mqtt=mqtt_settings
)

net_tx_sensor = Sensor(
    name=f"{HOST_NAME} Network TX",
    unique_id=f"{HOST_UUID}_network_tx_kbps",
    unit_of_measurement="kbps",
    device_class="data_rate",
    icon="mdi:upload-network",
    device=device_info,
    mqtt=mqtt_settings
)

net_rx_sensor = Sensor(
    name=f"{HOST_NAME} Network RX",
    unique_id=f"{HOST_UUID}_network_rx_kbps",
    unit_of_measurement="kbps",
    device_class="data_rate",
    icon="mdi:download-network",
    device=device_info,
    mqtt=mqtt_settings
)

# ========================
# Funkce pro čtení statistik z XO API
# ========================
def fetch_host_stats(xo_url, host_uuid, token, verify_ssl=True):
    headers = {"Cookie": f"authenticationToken={token}"}
    url = f"{xo_url.rstrip('/')}/rest/v0/hosts/{host_uuid}/stats"
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=verify_ssl)
        r.raise_for_status()
        data = r.json()
        stats = data.get("stats", {})
        end_ts = data.get("endTimestamp", int(time.time()))
        xo_interval = data.get("interval", 5)
        if not stats:
            log("XO API nevrátilo žádná data.", "WARNING")
            return {}

        # CPU
        aggregated_cpu = [0.0]*NUM_SAMPLES
        cpus = stats.get("cpus", {})
        num_cores = len(cpus)
        for core_data in cpus.values():
            samples = core_data[-NUM_SAMPLES:]
            samples += [0.0]*(NUM_SAMPLES - len(samples))
            for i in range(NUM_SAMPLES):
                aggregated_cpu[i] += samples[i]
        if num_cores > 0:
            aggregated_cpu = [v/num_cores for v in aggregated_cpu]

        # Memory
        mem_total = stats.get("memory", [0])[-NUM_SAMPLES:]
        mem_free = stats.get("memoryFree", [0])[-NUM_SAMPLES:]
        mem_used_pct = [round((t-f)/t*100,2) if t else 0 for t,f in zip(mem_total, mem_free)]
        mem_used_pct += [0.0]*(NUM_SAMPLES - len(mem_used_pct))

        # Síť
        pifs = stats.get("pifs", {})
        tx = pifs.get("tx", {}).get(str(NETWORK_INTERFACE), [])
        rx = pifs.get("rx", {}).get(str(NETWORK_INTERFACE), [])
        net_tx = [round(v*8/1000,2) for v in tx[-NUM_SAMPLES:]]
        net_rx = [round(v*8/1000,2) for v in rx[-NUM_SAMPLES:]]
        net_tx += [0.0]*(NUM_SAMPLES - len(net_tx))
        net_rx += [0.0]*(NUM_SAMPLES - len(net_rx))

        return {
            "cpu_total_load": aggregated_cpu,
            "memory_used_pct": mem_used_pct,
            "network_tx_kbps": net_tx,
            "network_rx_kbps": net_rx,
            "end_timestamp": end_ts,
            "xo_interval": xo_interval
        }
    except Exception as e:
        log(f"Chyba při fetch_host_stats: {e}", "ERROR")
        return {}

# ========================
# Publikace jednotlivých vzorků přes ha-mqtt-discoverable
# ========================
def publish_sample(buffer, index):
    cpu_sensor.set_state(round(buffer["cpu_total_load"][index],2))
    memory_sensor.set_state(round(buffer["memory_used_pct"][index],2))
    net_tx_sensor.set_state(round(buffer["network_tx_kbps"][index],2))
    net_rx_sensor.set_state(round(buffer["network_rx_kbps"][index],2))
    debug(f"Publikováno vzorek {index+1}/{NUM_SAMPLES}")

# ========================
# Hlavní smyčka
# ========================
def main():
    log(f"Spouštím XO MQTT Updater v{VERSION}")
    metrics_buffer = {}
    sample_index = NUM_SAMPLES
    last_fetch = 0

    while True:
        now = time.time()
        if sample_index >= NUM_SAMPLES or now - last_fetch >= UPDATE_INTERVAL:
            log("Stahuji nová data z XO API...")
            buffer = fetch_host_stats(XO_URL, HOST_UUID, XO_TOKEN, VERIFY_SSL)
            if buffer:
                metrics_buffer = buffer
                sample_index = 0
                last_fetch = now
            else:
                log("Nepodařilo se stáhnout data, pokusím se znovu za 5s.", "ERROR")
                time.sleep(PUBLISH_INTERVAL_S)
                continue

        if metrics_buffer and sample_index < NUM_SAMPLES:
            publish_sample(metrics_buffer, sample_index)
            sample_index += 1

        time.sleep(PUBLISH_INTERVAL_S)

if __name__ == "__main__":
    main()
