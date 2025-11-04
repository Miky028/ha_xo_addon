#!/usr/bin/env python3
import os
import json
import time
import requests
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta

# ========================
# KÓDEM DEFINOVANÁ VERZE (Změna pouze zde)
# ========================
VERSION = "1.2.19"

# ========================
# Globální konstanty
# ========================
PUBLISH_INTERVAL_S = 5 # Publikujeme každých 5 sekund

# ========================
# Funkce logování
# ========================
def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)

def debug(msg):
    if 'DEBUG' in globals() and DEBUG:
        log(msg, level="DEBUG")

# ========================
# Načtení konfigurace (Ostatní parametry)
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
# ==============================================================================
def on_connect(client, userdata, flags, rc):
    """Zpracovává výsledek připojení k brokeru."""
    if rc == 0:
        log("MQTT: Připojení k brokeru ÚSPĚŠNÉ. (Kód: 0)", "INFO")
        # Při úspěšném připojení hned publikujeme Discovery konfiguraci
        publish_discovery_config(client)
    elif rc == 5:
        log("MQTT: Připojení SELHALO - Chyba autentizace/autorizace. (Kód: 5)", "CRITICAL")
    else:
        log(f"MQTT: Připojení SELHALO. (Kód: {rc})", "CRITICAL")
        
def on_disconnect(client, userdata, rc):
    """Zpracovává odpojení od brokeru."""
    log(f"MQTT: Klient odpojen s kódem {rc}.", "WARNING")

def on_publish(client, userdata, mid):
    """Potvrzení doručení zprávy při QoS > 0."""
    debug(f"MQTT: Zpráva (ID: {mid}) ÚSPĚŠNĚ doručena brokeru.")
# ==============================================================================


# ========================
# FUNKCE PRO PUBLIKACI MQTT DISCOVERY
# ========================
def publish_discovery_config(client):
    """Publikuje konfigurační payload pro každý senzor (MQTT Discovery pro Home Assistant)."""
    global VERSION 
    
    if not HOST_UUID or not HOST_NAME:
        log("Chyba: Chybí HOST_UUID nebo HOST_NAME pro MQTT Discovery.", "ERROR")
        return
        
    device_info = {
        "identifiers": [f"xcp_ng_host_{HOST_UUID}"],
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
    
    # Jednotné téma, kam se posílá celý JSON objekt
    STATE_TOPIC = f"{MQTT_TOPIC}/{HOST_UUID}/state" 

    log("Publikuji konfigurační zprávy pro MQTT Discovery...")

    for key, (name, unit, icon, device_class) in metric_configs.items():
        # Použití pevného prefixu pro Discovery Topic
        discovery_topic = f"homeassistant/sensor/xcp_ng_host/{key}/config" 
        
        payload = {
            "name": f"{HOST_NAME} {name}",
            "unique_id": f"xcp_ng_{HOST_NAME}_{key}", 
            "state_topic": STATE_TOPIC, 
            "unit_of_measurement": unit,
            "icon": icon,
            "device_class": device_class,
            # ZMĚNA: Extrakce hodnoty z plochého JSONu
            "value_template": f"{{{{ value_json.{key} }}}}", 
            "force_update": True,
            "device": device_info
        }
        
        payload_bytes = json.dumps(payload).encode('utf-8')

        # Publikace Discovery zprávy s retain flagem
        client.publish(discovery_topic, payload_bytes, qos=1, retain=True)
        debug(f"Discovery publikováno pro {key} na téma: {discovery_topic}")
        
    log("Discovery konfigurace úspěšně publikována.")


# ========================
# Funkce pro čtení statistik hosta
# ========================
def fetch_host_stats(xo_url, host_uuid, token, verify_ssl=True):
    debug(f"Volání fetch_host_stats(xo_url={xo_url}, host_uuid={host_uuid}, token=****, verify_ssl={verify_ssl})")
    headers = {"Cookie": f"authenticationToken={token}"}
    url = f"{xo_url.rstrip('/')}/rest/v0/hosts/{host_uuid}/stats"
    
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=verify_ssl)
        r.raise_for_status()
        full_response = r.json()
        stats = full_response.get("stats", {})
        
        xo_interval = full_response.get("interval", 5) 
        end_timestamp = full_response.get("endTimestamp", int(time.time()))

        if xo_interval != PUBLISH_INTERVAL_S:
            log(f"XO vrací interval {xo_interval}s, očekáváno {PUBLISH_INTERVAL_S}s. To ovlivní synchronizaci.", "WARNING")

        if not stats:
            log("XO API nevrátilo žádná data.", "WARNING")
            return {}

        # ----------------------------------------------------
        # 1. LOGIKA PRO AGREGACI A PRŮMĚROVÁNÍ CPU
        # ----------------------------------------------------
        
        aggregated_cpu_series = [0.0] * NUM_SAMPLES 
        cpu_metrics_dict = stats.get("cpus", {})
        num_cpu_cores = 0
        
        if not cpu_metrics_dict:
             log("Nenalezen klíč 'cpus' pro CPU metriky.", "WARNING")
        else:
             num_cpu_cores = len(cpu_metrics_dict)
             
             for core_id, cpu_data in cpu_metrics_dict.items():
                 latest_samples = cpu_data[-NUM_SAMPLES:]
                 latest_samples += [0.0] * (NUM_SAMPLES - len(latest_samples))
                 
                 for i in range(NUM_SAMPLES):
                     aggregated_cpu_series[i] += latest_samples[i]

        if num_cpu_cores > 0:
            aggregated_cpu_series = [s / num_cpu_cores for s in aggregated_cpu_series]
            log(f"Agregováno a zprůměrováno {num_cpu_cores} CPU jader.")
        elif cpu_metrics_dict:
             log("Nalezena data CPU, ale počet jader je nula.", "WARNING")
             aggregated_cpu_series = [0.0] * NUM_SAMPLES

        # ----------------------------------------------------
        # 2. LOGIKA PRO PAMĚŤ
        # ----------------------------------------------------
        
        mem_total_series = stats.get("memory", [0])[-NUM_SAMPLES:]
        mem_free_series = stats.get("memoryFree", [0])[-NUM_SAMPLES:]
        mem_used_pct_series = []
        for t, f in zip(mem_total_series, mem_free_series):
            pct = round(((t - f) / t * 100) if t else 0, 2)
            mem_used_pct_series.append(pct)
        mem_used_pct_series += [0.0] * (NUM_SAMPLES - len(mem_used_pct_series))

        # ----------------------------------------------------
        # 3. LOGIKA PRO SÍŤOVÉ METRIKY: Převod na kbps
        # ----------------------------------------------------

        net_tx_kbps_series = [0.0] * NUM_SAMPLES
        net_rx_kbps_series = [0.0] * NUM_SAMPLES
        
        target_interface_id = str(NETWORK_INTERFACE) 
        pifs_metrics = stats.get("pifs", {})
        rx_metrics = pifs_metrics.get("rx", {})
        tx_metrics = pifs_metrics.get("tx", {})
        
        raw_net_tx = tx_metrics.get(target_interface_id, [])
        raw_net_rx = rx_metrics.get(target_interface_id, [])

        if not raw_net_tx and not raw_net_rx:
            log(f"Cílové síťové rozhraní '{target_interface_id}' nenalezeno.", "WARNING")
        else:
            net_tx_kbps_series = [round(v * 8 / 1000, 2) for v in raw_net_tx[-NUM_SAMPLES:]]
            net_rx_kbps_series = [round(v * 8 / 1000, 2) for v in raw_net_rx[-NUM_SAMPLES:]]
            
        net_tx_kbps_series += [0.0] * (NUM_SAMPLES - len(net_tx_kbps_series))
        net_rx_kbps_series += [0.0] * (NUM_SAMPLES - len(net_rx_kbps_series))
        
        
        # Vrácení bufferu
        return {
            "cpu_total_load": [round(v, 2) for v in aggregated_cpu_series],
            "memory_used_pct": mem_used_pct_series,
            "network_tx_kbps": net_tx_kbps_series,
            "network_rx_kbps": net_rx_kbps_series,
            "end_timestamp": end_timestamp, 
            "xo_interval": xo_interval
        }

    except requests.exceptions.RequestException as e:
        log(f"Chyba při připojení k XO API: {e}", "ERROR")
        return {}
    except Exception as e:
        log(f"Neočekávaná chyba při zpracování dat: {e}", "ERROR")
        return {}


# ========================
# MQTT publikace jednoho vzorku (JSON Payload)
# ========================
def publish_current_sample(client, topic, buffer, index):
    try:
        end_timestamp = buffer.get("end_timestamp", time.time())
        xo_interval = buffer.get("xo_interval", 5)
        sample_timestamp = end_timestamp - (NUM_SAMPLES - 1 - index) * xo_interval 
        
        cpu_load_value = buffer.get('cpu_total_load', [0.0]*NUM_SAMPLES)[index]
        log(f"Publikuji vzorek [{index+1}/{NUM_SAMPLES}] naměřený před ~{round(time.time() - sample_timestamp, 1)}s. Stav CPU: {cpu_load_value:.2f}%")

        # 1. Sestavení PLOCHÉHO JSON payloadu
        metrics_to_publish = {
            "uid": HOST_UUID, # Zachováno uid
            "cpu_total_load": buffer.get("cpu_total_load", [0.0] * NUM_SAMPLES)[index],
            "memory_used_pct": buffer.get("memory_used_pct", [0.0] * NUM_SAMPLES)[index],
            "network_tx_kbps": buffer.get("network_tx_kbps", [0.0] * NUM_SAMPLES)[index],
            "network_rx_kbps": buffer.get("network_rx_kbps", [0.0] * NUM_SAMPLES)[index],
        }
        
        # Konverze floatů na stringy (s formátováním) pro JSON payload. Plochá struktura.
        json_payload = {
            k: (v if k == "uid" else f"{v:.2f}") 
            for k, v in metrics_to_publish.items()
        }

        # 2. Určení stavového tématu
        state_topic = f"{topic}/{HOST_UUID}/state"
        
        # 3. Publikace JSON payloadu na jediné téma
        client.publish(state_topic, json.dumps(json_payload), qos=1, retain=False)
        debug(f"Publikováno JSON na téma: {state_topic} -> {json.dumps(json_payload)}")
            
    except Exception as e:
        log(f"Chyba při publikování do MQTT (index {index}): {e}", "ERROR")

# ========================
# Hlavní smyčka
# ========================
def main():
    client_id = f"xcp-ng-exporter"
    log(f"MQTT: Použitý Client ID: {client_id}")
    
    log("Inicializuji MQTT klienta...")
    client = mqtt.Client(client_id=client_id)
    
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish 

    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start() 
        
        log("Čekám 2 sekundy na navázání MQTT spojení a spuštění Discovery...")
        time.sleep(2) 
    except Exception as e:
        log(f"Chyba při inicializaci MQTT klienta: {e}", "CRITICAL")
        return

    # Globální stavové proměnné
    metrics_buffer = {}
    sample_index = NUM_SAMPLES 
    last_fetch_time = 0 
    
    log(f"MQTT klient připojen. Zahajuji cyklus: Stahování dat každých {UPDATE_INTERVAL}s, publikace každých {PUBLISH_INTERVAL_S}s.")

    while True:
        current_time = time.time()
        
        if sample_index >= NUM_SAMPLES or current_time - last_fetch_time >= UPDATE_INTERVAL:
            log("Stahuji novou sadu dat z XO API...")
            new_buffer = fetch_host_stats(XO_URL, HOST_UUID, XO_TOKEN, VERIFY_SSL)
            
            if new_buffer:
                metrics_buffer = new_buffer
                sample_index = 0
                last_fetch_time = current_time
                log(f"Data úspěšně stažena a připravena pro {NUM_SAMPLES} vzorků. Zahajuji publikaci.")
            else:
                log("Stažení dat selhalo. Přeskočuji publikaci a pokusím se znovu za 5s.", "ERROR")
                time.sleep(PUBLISH_INTERVAL_S)
                continue

        if metrics_buffer and sample_index < NUM_SAMPLES:
              publish_current_sample(client, MQTT_TOPIC, metrics_buffer, sample_index)
              sample_index += 1
        
        time.sleep(PUBLISH_INTERVAL_S)
        
    client.loop_stop()

# ========================
# Spuštění
# ========================
if __name__ == "__main__":
    log(f"Spouštím XO MQTT Updater v{VERSION} - Plynulá 5s publikace s {UPDATE_INTERVAL}s zpožděním sběru dat.")
    main()
