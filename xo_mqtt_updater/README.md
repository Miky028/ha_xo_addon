# XO MQTT Updater Add-on

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
- `mqtt_user` / `mqtt_password` – pro autentizaci na MQTT
- `update_interval` – frekvence aktualizace (v sekundách)

### Instalace z GitHubu:
1. Přidejte repozitář do Home Assistant Supervisor → Add-on Store → Repositories.  
2. Zadejte URL repozitáře.  
3. Nainstalujte add-on, nastavte konfiguraci a spusťte.
