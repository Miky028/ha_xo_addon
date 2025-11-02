#!/usr/bin/env python3
import argparse
import time
import json
import requests
import paho.mqtt.client as mqtt
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

METRICS = {
    "cpu": {"unit": "%", "name": "CPU Usage"},
    "memory": {"unit": "%", "name": "RAM Usage"},
    "disk": {"unit": "%", "name": "Disk Usage"},
    "network_tx": {"unit": "Mbps", "name": "Network TX"},
    "network_rx": {"unit": "Mbps", "name": "Network RX"}
}

DEBUG = False


def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)


def debug_log(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}", flush=True)


def str_to_bool(value):
    """Bezpečně převede 'true'/'false' stringy z CLI na bool."""
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def publish_discovery(client, host_uuid, host_name):
    debug_log(f"Volání publish_discovery(client, host_uuid={host_uuid}, host_name={host_name})")
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
        debug_log(f"Publishing discovery: {json.dumps(payload)} to {discovery_topic}")
        try:
            client.publish(discovery_topic, json.dumps(payload), retain=True)
        except Exception as e:
            log(f"Chyba při publikování Discovery pro {key}: {e}", "ERROR")


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
        debug_log(f"Data z XO API: {json.dumps(data)}")

        metrics = {
            "cpu": data.get("cpu", 0),
            "memory": data.get("memory", {}).get("usage", 0),
            "disk": data.get("disk", {}).get("usage", 0),
            "network_tx": data.get("network", {}).get("tx", 0) * 8 / 1_000_000,
            "network_rx": data.get("network", {}).get("rx", 0) * 8 / 1_000_000
        }
        debug_log(f"Parsované metriky: {metrics}")
        return metrics
    except requests.exceptions.RequestException as e:
        log(f"Chyba při připojení k XO API: {e}", "ERROR")
        return {}


def main():
    global DEBUG

    parser = argparse.ArgumentParser()
    parser.add_argument("--xo_url", required=True)
    parser.add_argument("--xo_token", required=True)
    parser.add_argument("--host_uuid", required=True)
    parser.add_argument("--host_name", required=True)
    parser.add_argument("--verify_ssl", default=False)
    parser.add_argument("--mqtt_host", required=True)
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_user", default="")
    parser.add_argument("--mqtt_password", default="")
    parser.add_argument("--update_interval", type=int, default=30)
    parser.add_argument("--debug", default=False)
    args = parser.parse_args()

    # Převod string -> bool
    args.verify_ssl = str_to_bool(args.verify_ssl)
    args.debug = str_to_bool(args.debug)
    DEBUG = args.debug

    # Výpis konfigurace na začátku
    log("Načtená konfigurace:")
    safe_cfg = {
        "xo_url": args.xo_url,
        "host_uuid": args.host_uuid,
        "host_name": args.host_name,
        "verify_ssl": args.verify_ssl,
        "mqtt_host": args.mqtt_host,
        "mqtt_port": args.mqtt_port,
        "mqtt_user": "***" if args.mqtt_user else "",
        "update_interval": args.update_interval,
        "debug": args.debug
    }
    for k, v in safe_cfg.items():
        log(f"  {k}: {v}")

    debug_log(f"Inicializuji MQTT klienta s host={args.mqtt_host}, port={args.mqtt_port}")
    client = mqtt.Client()
    if args.mqtt_user and args.mqtt_password:
        client.username_pw_set(args.mqtt_user, args.mqtt_password)
    try:
        client.connect(args.mqtt_host, args.mqtt_port)
        debug_log("Připojeno k MQTT brokeru")
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
                debug_log(f"Publikace metriky: {key}={value} na {state_topic}")
                try:
                    client.publish(state_topic, value)
                except Exception as e:
                    log(f"Chyba při publikování {key}: {e}", "ERROR")
        time.sleep(args.update_interval)


if __name__ == "__main__":
    main()
