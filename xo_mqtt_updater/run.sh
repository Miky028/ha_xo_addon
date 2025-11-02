#!/usr/bin/env bash
set -e

CONFIG_FILE="/data/options.json"

XO_URL=$(jq -r '.xo_url' $CONFIG_FILE)
HOST_UUID=$(jq -r '.host_uuid' $CONFIG_FILE)
USERNAME=$(jq -r '.username' $CONFIG_FILE)
PASSWORD=$(jq -r '.password' $CONFIG_FILE)
MQTT_HOST=$(jq -r '.mqtt_host' $CONFIG_FILE)
MQTT_PORT=$(jq -r '.mqtt_port' $CONFIG_FILE)
MQTT_USER=$(jq -r '.mqtt_user' $CONFIG_FILE)
MQTT_PASSWORD=$(jq -r '.mqtt_password' $CONFIG_FILE)
UPDATE_INTERVAL=$(jq -r '.update_interval' $CONFIG_FILE)

echo "[RUN.SH] Spouštím XO MQTT updater..." 

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
