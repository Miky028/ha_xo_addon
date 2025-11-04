#!/usr/bin/env python3
import os
import json
import time
import requests
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta

# ========================
# Globální konstanty
# ========================
PUBLISH_INTERVAL_S = 5 # Publikujeme každých 5 sekund

# ========================
# Funkce logování
# ========================
# Využíváme globální definici DEBUG (načtenou z configu)
def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)

def debug(msg):
    # Kontrola, zda je DEBUG nadefinován v globálním rozsahu
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
UPDATE_INTERVAL = int(cfg.get("update_interval", 30)) # Zde je 30s interval pro stahování dat
VERIFY_SSL = bool(cfg.get("verify_ssl", False))
DEBUG = bool(cfg.get("debug", False)) # Definováno globálně
NETWORK_INTERFACE = cfg.get("network_interface", "2") # vybraná síťovka (např. "2" pro druhý PIF)

# Ověření, že je interval sběru dat násobkem intervalu publikace
if UPDATE_INTERVAL % PUBLISH_INTERVAL_S != 0:
    log(f"Chyba: UPDATE_INTERVAL ({UPDATE_INTERVAL}s) musí být násobkem PUBLISH_INTERVAL_S ({PUBLISH_INTERVAL_S}s). Použiji 30s.", "ERROR")
    UPDATE_INTERVAL = 30
NUM_SAMPLES = UPDATE_INTERVAL // PUBLISH_INTERVAL_S

log("Načtená konfigurace:")
log(f"  XO_URL          = {XO_URL}")
log(f"  HOST_UUID       = {HOST_UUID}")
log(f"  HOST_NAME       = {HOST_NAME}")
log(f"  MQTT_HOST       = {MQTT_HOST}:{MQTT_PORT}")
log(f"  UPDATE_INTERVAL = {UPDATE_INTERVAL}s (stahování) / {PUBLISH_INTERVAL_S}s (publikace)")
log(f"  NETWORK_INTERFACE = {NETWORK_INTERFACE} (cílový index sítě)")


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
        
        # Zjištění skutečného intervalu XO a posledního timestampu
        xo_interval = full_response.get("interval", 5) 
        end_timestamp = full_response.get("endTimestamp", int(time.time()))

        if xo_interval != PUBLISH_INTERVAL_S:
            log(f"XO vrací interval {xo_interval}s, očekáváno {PUBLISH_INTERVAL_S}s. To ovlivní synchronizaci.", "WARNING")

        if not stats:
            log("XO API nevrátilo žádná data.", "WARNING")
            return {}

        # ----------------------------------------------------
        # 1. LOGIKA PRO AGREGACI A PRŮMĚROVÁNÍ CPU (Opraveno)
        # ----------------------------------------------------
        
        aggregated_cpu_series = [0.0] * NUM_SAMPLES 
        cpu_metrics_dict = stats.get("cpus", {})
        num_cpu_cores = 0
        
        if not cpu_metrics_dict:
             log("Nenalezen klíč 'cpus' pro CPU metriky. Zkontrolujte XO API response.", "WARNING")
        else:
             num_cpu_cores = len(cpu_metrics_dict)
             
             for core_id, cpu_data in cpu_metrics_dict.items():
                 latest_samples = cpu_data[-NUM_SAMPLES:]
                 latest_samples += [0.0] * (NUM_SAMPLES - len(latest_samples))
                 
                 for i in range(NUM_SAMPLES):
                     aggregated_cpu_series[i] += latest_samples[i]

        if num_cpu_cores > 0:
            # Vydělení součtu počtem jader pro získání průměru (max 100%)
            aggregated_cpu_series = [s / num_cpu_cores for s in aggregated_cpu_series]
            log(f"Agregováno a zprůměrováno {num_cpu_cores} CPU jader.")
        elif cpu_metrics_dict:
             log("Nalezena data CPU, ale počet jader je nula. Nastavuji zátěž na 0.", "WARNING")
             aggregated_cpu_series = [0.0] * NUM_SAMPLES

        # ----------------------------------------------------
        # 2. LOGIKA PRO PAMĚŤ
        # ----------------------------------------------------
        
        # Memory % Used
        mem_total_series = stats.get("memory", [0])[-NUM_SAMPLES:]
        mem_free_series = stats.get("memoryFree", [0])[-NUM_SAMPLES:]
        mem_used_pct_series = []
        for t, f in zip(mem_total_series, mem_free_series):
            pct = round(((t - f) / t * 100) if t else 0, 2)
            mem_used_pct_series.append(pct)
        mem_used_pct_series += [0.0] * (NUM_SAMPLES - len(mem_used_pct_series))

        # ----------------------------------------------------
        # 3. LOGIKA PRO SÍŤOVÉ METRIKY: Převod na kbps (Opraveno a robustní)
        # Načítání ze struktury stats['pifs']['rx'/'tx'][NETWORK_INTERFACE]
        # ----------------------------------------------------

        net_tx_kbps_series = [0.0] * NUM_SAMPLES
        net_rx_kbps_series = [0.0] * NUM_SAMPLES
        
        # Vynutíme konverzi na string, abychom měli jistotu, že klíč je správného typu
        target_interface_id = str(NETWORK_INTERFACE) 
        
        # 1. Získání slovníku PIF metrik ('pifs')
        pifs_metrics = stats.get("pifs", {})
        debug(f"PIFS: Nalezeno {len(pifs_metrics.keys())} klíčů v 'pifs' metrikách. Klíče: {list(pifs_metrics.keys())}")
        
        # 2. Získání RX a TX pod-slovníků
        rx_metrics = pifs_metrics.get("rx", {})
        tx_metrics = pifs_metrics.get("tx", {})
        
        debug(f"RX Metrics: Nalezeno {len(rx_metrics.keys())} klíčů. Pár prvních klíčů: {list(rx_metrics.keys())[:3]}")
        debug(f"TX Metrics: Nalezeno {len(tx_metrics.keys())} klíčů. Pár prvních klíčů: {list(tx_metrics.keys())[:3]}")
        
        # 3. Načtení dat pro cílové rozhraní (pomocí indexu z konfigurace)
        raw_net_tx = tx_metrics.get(target_interface_id, [])
        raw_net_rx = rx_metrics.get(target_interface_id, [])

        if not raw_net_tx and not raw_net_rx:
            log(f"Cílové síťové rozhraní '{target_interface_id}' nenalezeno ve struktuře pifs['rx'/'tx']. Nastavuji zátěž na 0.", "WARNING")
        else:
            # DEBUG VÝSTUP VZORKŮ
            debug(f"Načtené TX vzorky pro '{target_interface_id}' (prvních 5): {raw_net_tx[:5]}")
            debug(f"Načtené RX vzorky pro '{target_interface_id}' (prvních 5): {raw_net_rx[:5]}")
            debug(f"Celkem TX/RX vzorků v bufferu: {len(raw_net_tx)} / {len(raw_net_rx)}")
            
            # Slicing a přepočet z Byte/s na Kilobit/s (B/s * 8 / 1000)
            net_tx_kbps_series = [round(v * 8 / 1000, 2) for v in raw_net_tx[-NUM_SAMPLES:]]
            net_rx_kbps_series = [round(v * 8 / 1000, 2) for v in raw_net_rx[-NUM_SAMPLES:]]
            
            debug(f"Přepočtené TX (kbps) (posledních {NUM_SAMPLES}): {net_tx_kbps_series}")
            
        # Doplnění nulami
        net_tx_kbps_series += [0.0] * (NUM_SAMPLES - len(net_tx_kbps_series))
        net_rx_kbps_series += [0.0] * (NUM_SAMPLES - len(net_rx_kbps_series))
        
        
        # Vrácení bufferu (BEZ Disk IO metrik)
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
# MQTT publikace jednoho vzorku
# ========================
def publish_current_sample(client, topic, buffer, index):
    try:
        # Vzorky jsou indexovány od 0 (nejstarší) do 5 (nejnovější)
        # Timestamp daného vzorku pro logování
        end_timestamp = buffer.get("end_timestamp", time.time())
        xo_interval = buffer.get("xo_interval", 5)
        # Výpočet skutečného času, kdy byl vzorek pořízen (pro log)
        sample_timestamp = end_timestamp - (NUM_SAMPLES - 1 - index) * xo_interval 
        
        log(f"Publikuji vzorek [{index+1}/{NUM_SAMPLES}] naměřený před ~{round(time.time() - sample_timestamp, 1)}s. Stav CPU: {buffer.get('cpu_total_load', [0.0]*NUM_SAMPLES)[index]:.2f}%")

        # Metriky k publikaci (BEZ Disk IO metrik)
        metrics_to_publish = {
            "cpu_total_load": buffer.get("cpu_total_load", [0.0] * NUM_SAMPLES)[index],
            "memory_used_pct": buffer.get("memory_used_pct", [0.0] * NUM_SAMPLES)[index],
            "network_tx_kbps": buffer.get("network_tx_kbps", [0.0] * NUM_SAMPLES)[index],
            "network_rx_kbps": buffer.get("network_rx_kbps", [0.0] * NUM_SAMPLES)[index],
        }

        # Publikace každé metriky zvlášť (HA senzory)
        for key, value in metrics_to_publish.items():
            sub_topic = f"{topic}/{HOST_UUID}/{key}" # Používáme HOST_UUID
            # Použijeme f-string pro formátování na 2 desetinná místa, pokud je to float
            client.publish(sub_topic, f"{value:.2f}", qos=1, retain=False)
            debug(f"Publikováno: {sub_topic} -> {value:.2f}")
            
    except Exception as e:
        log(f"Chyba při publikování do MQTT (index {index}): {e}", "ERROR")

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
        client.loop_start() 
    except Exception as e:
        log(f"Chyba při připojení k MQTT brokeru: {e}", "ERROR")
        return

    # Globální stavové proměnné
    metrics_buffer = {}
    sample_index = NUM_SAMPLES # Začneme na indexu 6, abychom vynutili první fetch
    last_fetch_time = 0 
    
    log(f"MQTT klient připojen. Zahajuji cyklus: Stahování dat každých {UPDATE_INTERVAL}s, publikace každých {PUBLISH_INTERVAL_S}s.")

    while True:
        current_time = time.time()
        
        # 1. Kontrola, zda je potřeba obnovit data (buď vyčerpán index, nebo uplynul 30s interval)
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
                # Neobnovujeme index ani čas, pokusíme se znovu v dalším cyklu.
                time.sleep(PUBLISH_INTERVAL_S)
                continue # Přeskočí publikaci

        # 2. Publikace aktuálního vzorku (index 0 až 5)
        if metrics_buffer and sample_index < NUM_SAMPLES:
              publish_current_sample(client, MQTT_TOPIC, metrics_buffer, sample_index)
              sample_index += 1
        
        # 3. Čekání 5 sekund
        time.sleep(PUBLISH_INTERVAL_S)
        
    client.loop_stop()

# ========================
# Spuštění
# ========================
if __name__ == "__main__":
    log(f"Spouštím XO MQTT Updater v1.5.0 - Plynulá 5s publikace s {UPDATE_INTERVAL}s zpožděním sběru dat.")
    main()
