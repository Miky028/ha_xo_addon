#!/usr/bin/env bash

XO_URL=${XO_URL:-"http://xo.local"}
HOST_UUID=${HOST_UUID:-""}
USERNAME=${USERNAME:-"admin"}
PASSWORD=${PASSWORD:-"password"}
MQTT_HOST=${MQTT_HOST:-"core-mosquitto"}
MQTT_PORT=${MQTT_PORT:-1883}
MQTT_USER=${MQTT_USER:-""}
MQTT_PASSWORD=${MQTT_PASSWORD:-""}
UPDATE_INTERVAL=${UPDATE_INTERVAL:-30}

echo "Spouštím XO MQTT updater..."
exec python /xo_updater.py \
    --xo_url "$XO_URL" \
    --host_uuid "$HOST_UUID" \
    --username "$USERNAME" \
    --password "$PASSWORD" \
    --mqtt_host "$MQTT_HOST" \
    --mqtt_port "$MQTT_PORT" \
    --mqtt_user "$MQTT_USER" \
    --mqtt_password "$MQTT_PASSWORD" \
    --interval "$UPDATE_INTERVAL"
