#!/usr/bin/env bash
# načtení konfigurace z prostředí
XO_URL=${XO_URL:-"http://xo.local"}
HOST_UUID=${HOST_UUID:-""}
USERNAME=${USERNAME:-"admin"}
PASSWORD=${PASSWORD:-"password"}
MQTT_SERVER=${MQTT_SERVER:-"mqtt://core-mosquitto"}
UPDATE_INTERVAL=${UPDATE_INTERVAL:-30}

echo "Spouštím XO MQTT updater..."
exec python /xo_updater.py \
    --xo_url "$XO_URL" \
    --host_uuid "$HOST_UUID" \
    --username "$USERNAME" \
    --password "$PASSWORD" \
    --mqtt_server "$MQTT_SERVER" \
    --interval "$UPDATE_INTERVAL"
