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

def publish_discovery(client, host_uuid):
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
        client.publish(discovery_topic, json.dumps(payload), retain=True)

def fetch_metrics(xo_url, host_uuid, username, password):
    r = requests.get(f"{xo_url}/api/host/{host_uuid}", auth=(username, password))
    if r.status_code != 200:
        print(f"Chyba při volání XO API: {r.status_code}")
        return {}
    data = r.json().get("metrics", {})
    # převod network na Mbps
    if "network" in data:
        data["network"] = data["network"] * 8 / 1_000_000
    return data

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xo_url", required=True)
    parser.add_argument("--host_uuid", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--mqtt_server", required=True)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    client = mqtt.Client()
    client.connect(args.mqtt_server)

    publish_discovery(client, args.host_uuid)

    while True:
        metrics = fetch_metrics(args.xo_url, args.host_uuid, args.username, args.password)
        for key, value in metrics.items():
            state_topic = f"xo/{args.host_uuid}/{key}"
            client.publish(state_topic, value)
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
