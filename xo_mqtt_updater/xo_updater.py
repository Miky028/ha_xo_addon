#!/usr/bin/env python3
import argparse
import time
import json
import requests
import paho.mqtt.client as mqtt
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Mapování metrik, unit pro HA
METRICS = {
    "cpu": {"unit": "%", "name": "CPU Usage"},
    "memory": {"unit": "%", "name": "RAM Usage"},
    "disk": {"unit": "%", "name": "Disk Usage"},
    "network_tx": {"unit": "Mbps", "name": "Network TX"},
    "network_rx": {"unit": "Mbps", "name": "Network RX"}
}

def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)

def publish_discovery(client, host_uuid, host_name):
    log(f"Publikuji MQTT Discovery pro hosta {host_name}")
    for key, meta in METRICS.items():
        discovery_topic = f"homeassistant/sensor/xo_{host_uuid}_{key}/config"
        state_topic = f"xo/{host_uuid}/{key}"
        payload = {
            "name": f"{host_name} {meta['name']}",
            "state_topic": state_topic,
            "unit_of_measurement": meta["unit"],
            "unique_id": f"xo_{host_uuid}_{key}",
            "device": {
                "identifiers": [host_uuid],
                "name": host_name,
                "model": "host",
                "manufacturer": "Vates"
            }
        }
        try:
            client.publish(discovery_topic, json.dumps(payload), retain=True)
            log(f"Discovery publikováno: {discovery_topic}")
        except Exception as e:
            log(f"Chyba při publikování Discovery pro {key}: {e}", "ERROR")

def fetch_host_stats(xo_url, host_uuid, token, verify_ssl=false):
    log(f"Načítám statistiky hosta {host_uuid}")
    headers = {"Cookie": f"authenticationToken={token}"}
    url = f"{xo_url}/rest/v0/hosts/{host_uuid}/stats"
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=verify_ssl)
        if r.status_code != 200:
            log(f"Chyba XO API {r.status_code}: {r.text}", "WARNING")
            return {}
        data = r.json()

        # Parsování metrik do METRICS
        metrics = {}
        metrics["cpu"] = data.get("cpu", 0)
        metrics["memory"] = data.get("memory", {}).get("usage", 0)
        metrics["disk"] = data.get("disk", {}).get("usage", 0)

        # síť přepočít na Mbps
        metrics["network_tx"] = data.get("network", {}).get("tx", 0) * 8 / 1_000_000
        metrics["network_rx"] = data.get("network", {}).get("rx", 0) * 8 / 1_000_000

        return metrics
    except requests.exceptions.RequestException as e:
        log(f"Chyba při připojení k XO API: {e}", "ERROR")
        return {}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xo_url", required=True)
    parser.add_argument("--xo_token", required=True)
    parser.add_argument("--host_uuid", required=True)
    parser.add_argument("--host_name", required=True)
    parser.add_argument("--verify_ssl", type=bool, default=False)
    parser.add_argument("--mqtt_host", required=True)
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_user", default="")
    parser.add_argument("--mqtt_password", default="")
    parser.add_argument("--update_interval", type=int, default=30)
    args = parser.parse_args()

    client = mqtt.Client()
    if args.mqtt_user and args.mqtt_password:
        client.username_pw_set(args.mqtt_user, args.mqtt_password)
    try:
        client.connect(args.mqtt_host, args.mqtt_port)
    except Exception as e:
        log(f"Chyba při připojení k MQTT brokeru: {e}", "ERROR")
        exit(1)

    publish_discovery(client, args.host_uuid, args.host_name)

    log(f"Spouštím smyčku aktualizace každých {args.update_interval} sekund")
    while True:
        metrics = fetch_host_stats(
            args.xo_url, args.host_uuid, token=args.xo_token, verify_ssl=args.verify_ssl
        )
        if not metrics:
            log(f"Žádné metriky k publikování pro hosta {args.host_uuid}", "WARNING")
        else:
            for key, value in metrics.items():
                state_topic = f"xo/{args.host_uuid}/{key}"
                try:
                    client.publish(state_topic, value)
                    log(f"Publikováno {key}={value} na {state_topic}")
                except Exception as e:
                    log(f"Chyba při publikování {key}: {e}", "ERROR")
        time.sleep(args.update_interval)

if __name__ == "__main__":
    main()
