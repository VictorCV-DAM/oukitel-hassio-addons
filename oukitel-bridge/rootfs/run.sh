#!/usr/bin/with-contenv bashio
# ==============================================================================
# Home Assistant Community Add-on: Oukitel Cloud MQTT Bridge
# Generates config.json from Supervisor Add-on Options and runs bridge
# ==============================================================================

CONFIG_PATH="/data/options.json"
APP_CONFIG="/app/config.json"

bashio::log.info "Starting Oukitel Cloud MQTT Bridge Add-on..."

REGION=$(bashio::config 'region')
EMAIL=$(bashio::config 'email')
PASSWORD=$(bashio::config 'password')

MQTT_BROKER=$(bashio::config 'mqtt_broker')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USER=$(bashio::config 'mqtt_user')
MQTT_PASS=$(bashio::config 'mqtt_password')

INTERVAL=$(bashio::config 'polling_interval')
WAKE_INTERVAL=$(bashio::config 'wake_interval')

# Auto-discovery fallback for Home Assistant Core Mosquitto Add-on
if bashio::var.is_empty "${MQTT_BROKER}" || [ "${MQTT_BROKER}" = "core-mosquitto" ]; then
    if bashio::services.available "mqtt"; then
        bashio::log.info "Configuring MQTT via Home Assistant MQTT service..."
        MQTT_BROKER=$(bashio::services "mqtt" "host")
        MQTT_PORT=$(bashio::services "mqtt" "port")
        [ -z "${MQTT_USER}" ] && MQTT_USER=$(bashio::services "mqtt" "username")
        [ -z "${MQTT_PASS}" ] && MQTT_PASS=$(bashio::services "mqtt" "password")
    fi
fi

bashio::log.info "Configuring Bridge with Region: ${REGION}"

cat <<EOF > "${APP_CONFIG}"
{
  "cloud": {
    "region": "${REGION}",
    "email": "${EMAIL}",
    "password": "${PASSWORD}",
    "appid": "277",
    "appversion": "3.7.5"
  },
  "mqtt": {
    "broker": "${MQTT_BROKER}",
    "port": ${MQTT_PORT},
    "user": "${MQTT_USER}",
    "password": "${MQTT_PASS}",
    "topic_base": "homeassistant/sensor/oukitel"
  },
  "polling": {
    "interval_seconds": ${INTERVAL},
    "wake_interval_seconds": ${WAKE_INTERVAL}
  }
}
EOF

bashio::log.info "Launching Oukitel MQTT Bridge service..."
exec python3 /app/oukitel_cloud_mqtt.py
