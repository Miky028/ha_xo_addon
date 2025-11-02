# XCP-ng XO MQTT Updater Add-on

Add-on pro Home Assistant, který pravidelně čte metriky z XCP-ng hosta přes XO API a publikuje je do MQTT s podporou MQTT Discovery.

### Podporované senzory:
- CPU Usage (%)
- RAM Usage (%)
- Disk Usage (%)
- Network Usage (Mbps)

### Konfigurace Add-onu:
- `xo_url` – URL XO API
- `host_uuid` – UUID hosta
- `username` / `password` – přístup do XO API
- `mqtt_server` – MQTT broker
- `update_interval` – frekvence aktualizace (v sekundách)
