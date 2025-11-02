#!/usr/bin/env python3
import argparse
import time
import json
import requests
import paho.mqtt.client as mqtt
import urllib3

# Potlačení varování pro self-signed certifikáty
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

METRICS = {
    "cpu": {"unit": "%", "name": "CPU Usage"},
    "ram": {"unit": "%", "name": "RAM Usage"},
    "disk": {"unit": "%", "name": "Disk Usage"},
    "network_tx": {"unit": "Mbps", "name": "Network TX"},
    "network_rx": {"unit": "Mbps", "name": "Network RX"}
}

def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)

def publish_discovery(client, obj):
    obj_uuid = obj["uuid"]
    obj_name = obj.get("name", obj_uuid)
    log(f"Publikuji MQTT Discovery pro {obj['type']} {obj_name}")
    for key, meta in METRICS.items():
        discovery_topic = f"homeassistant/sensor/xo_{obj_uuid}_{key}/config"
        state_topic = f"xo/{obj_uuid}/{key}"
        payload = {
            "name": f"{obj_name} {meta['name']}",
            "state_topic": state_topic,
            "unit_of_measurement": meta["unit"],
            "unique_id": f"xo_{obj_uuid}_{key}",
            "device": {
                "identifiers": [obj_uuid],
                "name": obj_name,
                "model": obj["type"],
                "manufacturer": "Vates"
            }
        }
        try:
            client.publish(discovery_topic, json.dumps(payload), retain=True)
            log(f"Discovery publikováno: {discovery_topic}")
        except Exception as e:
            log(f"Chyba při publikování Discovery pro {key}: {e}", "ERROR")

def fetch_object_metrics(xo_url, obj, token, verify_ssl=True):
    obj_type = obj["type"]
    obj_uuid = obj["uuid"]
    log(f"Načítám metriky {obj_type} {obj_uuid}")
    headers = {"Cookie": f"authenticationToken={token}"}
    url = f"{xo_url}/rest/v0/{obj_type}s/{obj_uuid}?fields=metrics"
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=verify_ssl)
        if r.status_code != 200:
            log(f"Chyba XO API {r.status_code}: {r.text}", "WARNING")
            return {}
        data = r.json().get("metrics", {})

        if "network_tx" in data:
            data["network_tx"] = data["network_tx"] * 8 / 1_000_000
        if "network_rx" in data:
            data["network_rx"] = data["network_rx"] * 8 / 1_000_000

        return data
    except requests.exceptions.RequestException as e:
        log(f"Chyba při připojení k XO API: {e}", "ERROR")
        return {}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xo_url", required=True)
    parser.add_argument("--xo_token", required=True)
    parser.add_argument("--verify_ssl", type=bool, default=True)
    parser.add_argument("--mqtt_host", required=True)
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_user", default="")
    parser.add_argument("--mqtt_password", default="")
    parser.add_argument("--update_interval", type=int, default=30)
    parser.add_argument("--objects", type=str, required=True, help="JSON list of objects")
    args = parser.parse_args()

    try:
        objects = json.loads(args.objects)
    except Exception as e:
        log(f"Chyba při čtení seznamu objektů: {e}", "ERROR")
        exit(1)

    client = mqtt.Client()
    if args.mqtt_user and args.mqtt_password:
        client.username_pw_set(args.mqtt_user, args.mqtt_password)
    try:
        client.connect(args.mqtt_host, args.mqtt_port)
    except Exception as e:
        log(f"Chyba při připojení k MQTT brokeru: {e}", "ERROR")
        exit(1)

    for obj in objects:
        publish_discovery(client, obj)

    log(f"Spouštím smyčku aktualizace každých {args.update_interval} sekund")
    while True:
        for obj in objects:
            metrics = fetch_object_metrics(
                args.xo_url, obj, token=args.xo_token, verify_ssl=args.verify_ssl
            )
            if not metrics:
                log(f"Žádné metriky k publikování pro {obj['uuid']}", "WARNING")
                continue
            for key, value in metrics.items():
                state_topic = f"xo/{obj['uuid']}/{key}"
                try:
                    client.publish(state_topic, value)
                    log(f"Publikováno {key}={value} na {state_topic}")
                except Exception as e:
                    log(f"Chyba při publikování {key}: {e}", "ERROR")
        time.sleep(args.update_interval)

if __name__ == "__main__":
    main()
