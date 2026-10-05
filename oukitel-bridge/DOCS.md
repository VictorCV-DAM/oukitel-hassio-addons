# Home Assistant Community Add-on: Oukitel Power Station MQTT Bridge

Official Home Assistant Add-on to connect **Oukitel Portable Power Stations** (P2001 Plus, P2001, P5000, BP2000, etc.) to Home Assistant via MQTT Discovery.

## Features

- **Dual Transport (Local LAN + Cloud Fallback)**: Direct local LAN push (TCP 6607, AES-128-CBC) for instant real-time telemetry, with seamless Cloud polling fallback.
- **Individual Port Telemetry**: Dedicated power monitoring for all 4 USB Type-C ports, USB-A, USB-C QC, 12V DC car socket (power, voltage, current), and 230V AC inverter output.
- **Native Hardware Fault Status ENUM**: Exposes `sensor.oukitel_hardware_fault_status` with standardized states (`Normal`, `High Temperature Warning`, `Over-Temperature`, `Under-Temperature`, `Low Battery Warning`, `Critical Low Battery`, `Overload Protection`, `Hardware Fault`) for direct dropdown selection in automations.
- **Smart Physics-Based Autonomy**: Real-time net power balance calculation ($$\text{net\_power} = \text{total\_input} - \text{total\_output}$$) providing true battery drain and recharge countdowns, bypassing stock firmware 99-hour overflows.
- **No Smartphone / No ADB Needed**: Fully autonomous daemon running straight inside your Home Assistant host.
- **MQTT Auto-Discovery**: Automatically creates all sensors, switches, numbers, and selectors in Home Assistant.
- **Interactive Controls**: Bidirectional remote control of AC 230V, DC 12V, and USB ports, AC charging rate limit slider (3-100%), and inverter frequency/voltage selectors.
- **Autonomous Keep-Alive**: Periodically keeps device reporting active so it never enters sleep mode while connected.

## Configuration

In the Add-on Configuration tab, fill in the following options:

```yaml
region: "EU"
connection_mode: "auto"
lan_host: ""
email: "your_wonderfree_app_email@example.com"
password: "your_app_password"
mqtt_broker: "core-mosquitto"
mqtt_port: 1883
mqtt_user: ""
mqtt_password: ""
polling_interval: 10
wake_interval: 25
```

- **region**: Server region (`EU` for Europe, `US` for North America, `CN` for China/Asia).
- **connection_mode**: Connection strategy (`auto` for LAN preferred + Cloud fallback, `lan` for local LAN only, `cloud` for cloud only).
- **lan_host**: Optional static IP address of the station on your local network (leave blank for automatic UDP discovery).
- **email / password**: Your official Wonderfree app login credentials.
- **mqtt_broker**: Your MQTT broker address (default `core-mosquitto` if using the official HA Mosquitto add-on).
- **polling_interval**: Polling interval in seconds (default `10`).
- **wake_interval**: Firmware keep-alive broadcast interval in seconds (default `25`).
