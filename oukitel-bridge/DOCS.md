# Home Assistant Community Add-on: Oukitel Cloud MQTT Bridge

Official Home Assistant Add-on to connect **Oukitel Portable Power Stations** (P2001 Plus, P2001, P5000, BP2000, etc.) to Home Assistant via MQTT Discovery.

## Features

- **No Smartphone / No ADB Needed**: Directly authenticates against the Wonderfree / Acceleronix Cloud API.
- **MQTT Auto-Discovery**: Automatically creates all sensors and switches in Home Assistant.
- **Real-Time Telemetry**: Battery SOC, Input Power (Total, AC, Solar DC), Output Power, Temperatures, Estimated Remaining Times, and Wi-Fi RSSI.
- **Interactive Switches**: Full bidirectional control of AC Out, DC 12V Out, and USB ports.
- **Autonomous Keep-Alive**: Periodically keeps device reporting active so it never enters sleep mode while connected.

## Configuration

In the Add-on Configuration tab, fill in the following options:

```yaml
region: "EU"
email: "your_wonderfree_app_email@example.com"
password: "your_password"
mqtt_broker: "core-mosquitto"
mqtt_port: 1883
mqtt_user: ""
mqtt_password: ""
polling_interval: 10
wake_interval: 25
```

- **region**: Server region (`EU` for Europe, `US` for North America, `CN` for China/Asia).
- **email / password**: Your official Wonderfree app login credentials.
- **mqtt_broker**: Your MQTT broker address (default `core-mosquitto` if using the official HA Mosquitto add-on).
- **polling_interval**: Polling interval in seconds (default `10`).
- **wake_interval**: Firmware keep-alive broadcast interval in seconds (default `25`).
