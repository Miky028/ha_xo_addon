import argparse
import time
import json
import requests
import paho.mqtt.client as mqtt

METRICS = {
    "cpu": {"unit": "%", "name": "CPU Usage"},
    "ram": {"unit": "%", "name": "RAM Usage"},
    "disk": {"unit": "%", "name": "Disk Usage"},
    "network": {"unit": "Mbps", "name": "Network Usage"}
}

def log(msg):
    print(f"[XO MQTT Updater] {msg}")

def publish_discovery(client, host_uuid):
    log(f"Publikuji MQTT Discovery pro host {host_uuid}")
    for key, meta in METRICS.items():
        discovery_topic = f"homeassistant/sensor/xo_{host_uuid}_{key}/config"
        state_topic = f"xo/{host_uuid}/{key}"
        payload = {
            "name": meta["name"],
            "state_topic": state_topic,
            "unit_of_measurement": meta["unit"],
            "unique_id": f"xo_{host_uuid}_{key}",
            "device": {
                "identifiers": [host_uuid],
                "name": f"XO Host {host_uuid}",
                "model": "XCP-ng",
                "manufacturer": "Vates"
            }
        }
        try:
            client.publish(discovery_topic, json.dumps(payload), retain=True)
            log(f"Discovery publikováno na {discovery_topic}: {payload}")
        except Exception as e:
            log(f"Chyba při publikování Discovery pro {key}: {e}")

def fetch_metrics(xo_url, host_uuid, username, password):
    log(f"Načítám metriky z XO API host {host_uuid}")
    try:
        r = requests.get(f"{xo_url}/api/host/{host_uuid}", auth=(username, password), timeout=10)
        if r.status_code != 200:
            log(f"Chyba XO API: {r.status_code} {r.text}")
            return {}
        data = r.json().get("metrics", {})
        if "network" in data:
            data["network"] = data["network"] * 8 / 1_000_000  # Mbps
        log(f"Načtené metriky: {data}")
        return data
    except Exception as e:
        log(f"Chyba při připojení k XO API: {e}")
        return {}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xo_url", required=True)
    parser.add_argument("--host_uuid", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--mqtt_host", required=True)
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_user", default="")
    parser.add_argument("--mqtt_password", default="")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    log("Inicializuji MQTT klienta...")
    client = mqtt.Client()
    if args.mqtt_user and args.mqtt_password:
        client.username_pw_set(args.mqtt_user, args.mqtt_password)
        log(f"Používám MQTT uživatele {args.mqtt_user}")
    try:
        client.connect(args.mqtt_host, args.mqtt_port)
        log(f"Připojeno k MQTT brokeru {args.mqtt_host}:{args.mqtt_port}")
    except Exception as e:
        log(f"Chyba při připojení k MQTT brokeru: {e}")
        return

    publish_discovery(client, args.host_uuid)

    log(f"Spouštím smyčku aktualizace každých {args.interval} sekund")
    while True:
        metrics = fetch_metrics(args.xo_url, args.host_uuid, args.username, args.password)
        if not metrics:
            log("Žádné metriky k publikování")
        for key, value in metrics.items():
            state_topic = f"xo/{args.host_uuid}/{key}"
            try:
                client.publish(state_topic, value)
                log(f"Publikováno {key}={value} na {state_topic}")
            except Exception as e:
                log(f"Chyba při publikování {key}: {e}")
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
