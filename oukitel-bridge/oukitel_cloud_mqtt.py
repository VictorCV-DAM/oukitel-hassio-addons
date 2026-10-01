"""
================================================================================
OUKITEL POWER STATION CLOUD API -> HOME ASSISTANT MQTT BRIDGE (Standalone Daemon)
================================================================================
- 100% native and direct connection to the official Acceleronix / Wonderfree Cloud API.
- ZERO smartphone or Android ADB dependencies: fully autonomous daemon.
- Bidirectional switch controls: toggle AC 230V, DC 12V, and USB directly from Home Assistant.
- Autonomous sleep keep-alive via high_frequency_reporting.
- Real-time telemetry: battery SOC, total/AC/DC input power, output power, temperature, times.
- Transparent OAuth session token auto-renewal.
- Home Assistant MQTT Auto-Discovery for all sensors and switches.
- Standalone and portable across Windows, Linux, and macOS.
================================================================================
"""

import hashlib
import json
import logging
import os
import random
import string
import sys
import time
from base64 import b64encode
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# Localizar la ruta base
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
ICON_FILE = os.path.join(BASE_DIR, "icon.ico")


def set_windows_console_icon():
    """Sets the console window and taskbar icon on Windows if icon.ico is present."""
    if not sys.platform.startswith("win"):
        return
    if not os.path.exists(ICON_FILE):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            hicon_small = ctypes.windll.user32.LoadImageW(None, ICON_FILE, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            hicon_big = ctypes.windll.user32.LoadImageW(None, ICON_FILE, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            if hicon_small:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
            if hicon_big:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
    except Exception:
        pass


# Configuration of available cloud server regions
# [NOTE]: 'EU' is the primary verified region tested on physical hardware.
# 'US' and 'CN' are included as [EXPERIMENTAL] based on official APK endpoints.
REGION_SERVERS = {
    "EU": {
        "name": "Europe (Verified)",
        "base_url": "https://iot-api.acceleronix.io",
        "user_domain": "E.SP.4294967410",
        "user_domain_secret": "3aRNUwWahjyANa7WfBK2wCCkxCexB6nXxKJwXxfePvzf",
        "appid": "277",
        "appversion": "3.7.5"
    },
    "US": {
        "name": "North America / USA [EXPERIMENTAL]",
        "base_url": "https://iot-api.quectelus.com",
        "user_domain": "E.SP.4294967410",
        "user_domain_secret": "3aRNUwWahjyANa7WfBK2wCCkxCexB6nXxKJwXxfePvzf",
        "appid": "277",
        "appversion": "3.7.5"
    },
    "CN": {
        "name": "China / Asia [EXPERIMENTAL]",
        "base_url": "https://iot-api.quectelcn.com",
        "user_domain": "E.SP.4294967410",
        "user_domain_secret": "3aRNUwWahjyANa7WfBK2wCCkxCexB6nXxKJwXxfePvzf",
        "appid": "277",
        "appversion": "3.7.5"
    }
}

# Valores por defecto
DEFAULT_CONFIG = {
    "cloud": {
        "region": "EU",
        "email": "your_email@example.com",
        "password": "your_password",
        "base_url": REGION_SERVERS["EU"]["base_url"],
        "user_domain": REGION_SERVERS["EU"]["user_domain"],
        "user_domain_secret": REGION_SERVERS["EU"]["user_domain_secret"],
        "appid": REGION_SERVERS["EU"]["appid"],
        "appversion": REGION_SERVERS["EU"]["appversion"]
    },
    "mqtt": {
        "broker": "192.168.1.XXX",
        "port": 1883,
        "user": "",
        "password": "",
        "topic_base": "homeassistant/sensor/oukitel"
    },
    "polling": {
        "interval_seconds": 10,
        "wake_interval_seconds": 25
    }
}

# Cargar configuración desde config.json
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"Error al leer {CONFIG_FILE}, usando valores por defecto: {e}")
        cfg = DEFAULT_CONFIG
else:
    cfg = DEFAULT_CONFIG

cloud_cfg = cfg.get("cloud", {})
SELECTED_REGION = str(cloud_cfg.get("region", "EU")).upper()

# Resolución de región (EU / US / CN / CUSTOM)
if SELECTED_REGION in REGION_SERVERS:
    reg_data = REGION_SERVERS[SELECTED_REGION]
    BASE_URL           = reg_data["base_url"]
    USER_DOMAIN        = reg_data["user_domain"]
    USER_DOMAIN_SECRET = reg_data["user_domain_secret"]
    APP_ID             = reg_data["appid"]
    APP_VERSION        = reg_data["appversion"]
else:
    # Región personalizada o valores manuales en config.json
    SELECTED_REGION = "CUSTOM"
    BASE_URL           = cloud_cfg.get("base_url",           DEFAULT_CONFIG["cloud"]["base_url"])
    USER_DOMAIN        = cloud_cfg.get("user_domain",        DEFAULT_CONFIG["cloud"]["user_domain"])
    USER_DOMAIN_SECRET = cloud_cfg.get("user_domain_secret", DEFAULT_CONFIG["cloud"]["user_domain_secret"])
    APP_ID             = cloud_cfg.get("appid",              DEFAULT_CONFIG["cloud"]["appid"])
    APP_VERSION        = cloud_cfg.get("appversion",         DEFAULT_CONFIG["cloud"]["appversion"])

CLOUD_EMAIL    = cloud_cfg.get("email",    DEFAULT_CONFIG["cloud"]["email"])
CLOUD_PASSWORD = cloud_cfg.get("password", DEFAULT_CONFIG["cloud"]["password"])

MQTT_BROKER  = cfg.get("mqtt", {}).get("broker",     DEFAULT_CONFIG["mqtt"]["broker"])
MQTT_PORT    = int(cfg.get("mqtt", {}).get("port",   DEFAULT_CONFIG["mqtt"]["port"]))
MQTT_USER    = cfg.get("mqtt", {}).get("user",       DEFAULT_CONFIG["mqtt"]["user"])
MQTT_PASS    = cfg.get("mqtt", {}).get("password",   DEFAULT_CONFIG["mqtt"]["password"])
TOPIC_BASE   = cfg.get("mqtt", {}).get("topic_base", DEFAULT_CONFIG["mqtt"]["topic_base"])
AVAILABILITY_TOPIC = f"{TOPIC_BASE}/availability"

INTERVALO_SEGUNDOS      = int(cfg.get("polling", {}).get("interval_seconds", DEFAULT_CONFIG["polling"]["interval_seconds"]))
INTERVALO_WAKE_SEGUNDOS = int(cfg.get("polling", {}).get("wake_interval_seconds", 25))

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("OukitelCloud")


# ==========================================
# --- CLIENTE CLOUD ACCELERONIX ---
# ==========================================
class AcceleronixCloudClient:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = 0
        self.device_key = None
        self.product_key = None
        self.device_name = None

    def _encrypt_password(self, password: str, random_str: str) -> str:
        """Cifrado AES-128-CBC requerido por el protocolo de login de Quectel/Acceleronix."""
        md5_hash = hashlib.md5(random_str.encode('utf-8')).hexdigest().upper()
        aes_key = md5_hash[8:24].encode('utf-8')
        iv = (md5_hash[16:24] + md5_hash[8:16]).encode('utf-8')
        cipher = AES.new(aes_key, AES.MODE_CBC, iv)
        padded = pad(password.encode('utf-8'), 16)
        return b64encode(cipher.encrypt(padded)).decode('utf-8')

    def _calculate_signature(self, email: str, pwd_b64: str, random_str: str) -> str:
        """Firma SHA-256 del login."""
        raw = email + pwd_b64 + random_str + USER_DOMAIN_SECRET
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def login(self) -> bool:
        """Authenticate with Acceleronix / Quectel Cloud with auto-renewal."""
        log.info("Authenticating with Acceleronix / Quectel Cloud...")
        chars = string.ascii_letters + string.digits
        random_str = ''.join(random.choice(chars) for _ in range(16))

        pwd_encrypted = self._encrypt_password(self.password, random_str)
        signature = self._calculate_signature(self.email, pwd_encrypted, random_str)

        url = f"{BASE_URL}/v2/enduser/enduserapi/emailPwdLogin"
        headers = {
            "appversion": APP_VERSION,
            "appsystemtype": "android",
            "appid": APP_ID,
            "User-Agent": "okhttp/4.9.3",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        payload = {
            "email": self.email,
            "pwd": pwd_encrypted,
            "random": random_str,
            "userDomain": USER_DOMAIN,
            "signature": signature
        }

        try:
            r = requests.post(url, headers=headers, data=payload, timeout=15)
            res = r.json()
            if res.get("code") == 200:
                data = res["data"]
                self.access_token = data["accessToken"]["token"]
                self.token_expiry = data["accessToken"].get("expirationTime", int(time.time()) + 7000)
                self.refresh_token = data["refreshToken"]["token"]
                log.info("✅ Successfully authenticated. Token valid until: %s (auto-renewable)",
                         time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.token_expiry)))
                return True
            else:
                log.error("❌ Login error: %s (code %s)", res.get("msg"), res.get("code"))
                return False
        except Exception as e:
            log.error("⚠️ Exception during cloud login: %s", e)
            return False

    def ensure_authenticated(self):
        """Ensures the access token is valid; auto-renews 120s before expiry or if null."""
        if not self.access_token or time.time() > (self.token_expiry - 120):
            log.info("🔄 Token expiring or null. Automatically renewing session...")
            return self.login()
        return True

    def get_auth_headers(self) -> dict:
        return {
            "appversion": APP_VERSION,
            "appsystemtype": "android",
            "appid": APP_ID,
            "Authorization": self.access_token,
            "User-Agent": "okhttp/4.9.3"
        }

    def fetch_device_info(self) -> bool:
        """Fetches device identifier (deviceKey and productKey)."""
        self.ensure_authenticated()
        url = f"{BASE_URL}/v2/binding/enduserapi/userDeviceList"
        try:
            r = requests.get(url, headers=self.get_auth_headers(), timeout=15)
            res = r.json()
            if res.get("code") == 200:
                devices = res.get("data", {}).get("list", [])
                if devices:
                    dev = devices[0]
                    self.device_key = dev["deviceKey"]
                    self.product_key = dev["productKey"]
                    self.device_name = dev.get("deviceName", "Oukitel P2001")
                    log.info("🔋 Power station detected: %s (DK: %s, PK: %s)",
                             self.device_name, self.device_key, self.product_key)
                    return True
                else:
                    log.error("❌ No devices found registered in this account.")
                    return False
            elif res.get("code") == 5032:
                log.warning("Token expired, retrying login...")
                self.login()
                return self.fetch_device_info()
            else:
                log.error("Error retrieving device list: %s", res.get("msg"))
                return False
        except Exception as e:
            log.error("Exception in fetch_device_info: %s", e)
            return False

    def control_device(self, properties_list: list) -> bool:
        """
        Sends hardware control commands to the power station via the official Acceleronix REST API.
        properties_list: List of dicts, e.g. [{"ac_switch": True}], [{"high_frequency_reporting": 3}]
        """
        self.ensure_authenticated()
        if not self.device_key or not self.product_key:
            if not self.fetch_device_info():
                return False

        url = f"{BASE_URL}/v2/binding/enduserapi/batchControlDevice"
        headers = {**self.get_auth_headers(), "Content-Type": "application/json"}
        payload = {
            "data": json.dumps(properties_list),
            "deviceList": [{"deviceKey": self.device_key, "productKey": self.product_key}],
            "cacheTime": 60,
            "isCache": 1,
            "isCover": 1,
            "dataFormat": 0,
            "type": 2
        }

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=10)
            res = r.json()
            code = res.get("code")

            if code == 200:
                tickets = [item.get("ticket") for item in res.get("data", {}).get("successList", [])]
                log.info("⚡ [Cloud Command] Successfully sent %s (Ticket: %s)", properties_list, tickets)
                return True
            elif code == 5032:
                log.warning("Token expired in control_device, renewing session...")
                self.login()
                return self.control_device(properties_list)
            else:
                log.error("❌ Cloud control error: %s (code %s)", res.get("msg"), code)
                return False
        except Exception as e:
            log.error("⚠️ Exception sending control command: %s", e)
            return False

    def wake_device(self) -> bool:
        """
        Wakes and keeps the battery active by requesting high-frequency reporting (Wi-Fi + LAN).
        high_frequency_reporting = 3
        """
        log.info("🔔 [Keep-Alive] Sending high-frequency reporting keep-alive command...")
        return self.control_device([{"high_frequency_reporting": 3}])

    def get_telemetry(self) -> dict:
        """Queries real-time telemetry from the Cloud API."""
        self.ensure_authenticated()
        if not self.device_key or not self.product_key:
            if not self.fetch_device_info():
                return {}

        url = f"{BASE_URL}/v2/binding/enduserapi/getDeviceBusinessAttributes"
        params = {"pk": self.product_key, "dk": self.device_key}

        try:
            r = requests.get(url, headers=self.get_auth_headers(), params=params, timeout=10)
            res = r.json()

            if res.get("code") == 5032:
                log.warning("Token expired during telemetry fetch. Renewing session...")
                self.login()
                return self.get_telemetry()

            if res.get("code") != 200:
                log.warning("Non-OK telemetry response: %s", res)
                return {}

            data = res.get("data", {})
            device_data = data.get("deviceData", {})
            tsl_list = data.get("customizeTslInfo", [])

            is_online = bool(
                device_data.get("isOnline",
                device_data.get("onlineStatus",
                device_data.get("online", True)))
            )

            metrics = {
                "online": is_online,
                "wifi_signal": device_data.get("signalStrength", -100),
            }

            for item in tsl_list:
                code    = item.get("resourceCode")
                val_raw = item.get("resourceValce")
                dtype   = item.get("dataType")

                if not code or val_raw is None:
                    continue

                if dtype == "INT":
                    try:
                        metrics[code] = int(val_raw)
                    except ValueError:
                        metrics[code] = 0
                elif dtype == "BOOL":
                    metrics[code] = (str(val_raw).lower() == "true")
                elif dtype == "STRUCT":
                    try:
                        metrics[code] = json.loads(val_raw)
                    except Exception:
                        metrics[code] = val_raw
                else:
                    metrics[code] = val_raw

            return metrics

        except Exception as e:
            log.error("Excepción al consultar telemetría: %s", e)
            return {}


# ==========================================
# --- MQTT AND HOME ASSISTANT DISCOVERY ---
# ==========================================
def configure_ha_discovery(client: mqtt.Client):
    """Publishes Home Assistant MQTT Discovery configuration for sensors and switches."""
    device_info = {
        "identifiers": ["oukitel_p2001_station"],
        "name": "Oukitel P2001",
        "model": "P2001 Plus",
        "manufacturer": "OUKITEL",
        "sw_version": "Standalone Cloud MQTT Bridge 1.0.0"
    }

    # Telemetry sensors (read-only)
    sensors = [
        ("battery",              "Oukitel Battery",             "%",   "battery",         "measurement", "mdi:battery-charging"),
        ("input_watts",          "Total Input Power",          "W",   "power",           "measurement", "mdi:solar-power"),
        ("output_watts",         "Total Output Power",         "W",   "power",           "measurement", "mdi:flash"),
        ("temperature",          "Oukitel Temperature",        "°C",  "temperature",     "measurement", "mdi:thermometer"),
        ("ac_input_watts",       "AC Input Power",             "W",   "power",           "measurement", "mdi:transmission-tower"),
        ("dc_input_watts",       "DC Solar Input Power",       "W",   "power",           "measurement", "mdi:solar-panel"),
        ("remain_time",          "Remaining Discharge Time",   "min", "duration",        "measurement", "mdi:timer-outline"),
        ("remain_charging_time", "Remaining Charge Time",      "min", "duration",        "measurement", "mdi:timer-sand"),
        ("ac_charging_limit",    "AC Charging Limit",          "%",   None,              "measurement", "mdi:gauge"),
        ("wifi_signal",          "WiFi Signal",                "dBm", "signal_strength", "measurement", "mdi:wifi"),
    ]

    for id_sensor, name, unit, dev_class, state_cl, icon in sensors:
        config_topic = f"homeassistant/sensor/oukitel_{id_sensor}/config"
        payload = {
            "name": name,
            "state_topic": f"{TOPIC_BASE}/{id_sensor}",
            "unique_id": f"oukitel_p2001_{id_sensor}",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info,
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if dev_class:
            payload["device_class"] = dev_class
        if state_cl:
            payload["state_class"] = state_cl
        if icon:
            payload["icon"] = icon
        client.publish(config_topic, json.dumps(payload), retain=True)

    # Switches (bidirectional remote controls)
    switches = [
        ("ac_switch",  "AC Output",     "mdi:power-socket-eu"),
        ("dc_switch",  "DC 12V Output", "mdi:car-electric"),
        ("usb_switch", "USB Output",    "mdi:usb-port"),
    ]
    for id_sw, name, icon in switches:
        config_topic = f"homeassistant/switch/oukitel_{id_sw}/config"
        payload = {
            "name": name,
            "state_topic":   f"{TOPIC_BASE}/{id_sw}",
            "command_topic": f"{TOPIC_BASE}/{id_sw}/set",
            "payload_on":    "ON",
            "payload_off":   "OFF",
            "state_on":      "ON",
            "state_off":     "OFF",
            "icon": icon,
            "unique_id": f"oukitel_p2001_{id_sw}",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available":     "online",
            "payload_not_available": "offline",
            "device": device_info,
            "optimistic": False,
        }
        client.publish(config_topic, json.dumps(payload), retain=True)

    log.info("✅ Entities registered in Home Assistant MQTT Discovery.")


# ==========================================
# --- MAIN LOOP ---
# ==========================================
def main():
    set_windows_console_icon()
    log.info("Starting Oukitel Cloud MQTT Bridge (100% Autonomous - Zero Mobile/ADB)...")
    log.info("Configuration file: %s", CONFIG_FILE)
    if SELECTED_REGION in REGION_SERVERS:
        reg_info = REGION_SERVERS[SELECTED_REGION]
        log.info("🌍 Selected Cloud Region: %s - %s (%s)", SELECTED_REGION, reg_info["name"], BASE_URL)
        if SELECTED_REGION != "EU":
            log.warning("⚠️ [EXPERIMENTAL NOTICE]: Region '%s' is based on official endpoints but unverified on physical units.", SELECTED_REGION)
    else:
        log.info("🌍 Selected Cloud Region: CUSTOM (%s)", BASE_URL)

    cloud = AcceleronixCloudClient(CLOUD_EMAIL, CLOUD_PASSWORD)
    if not cloud.login():
        log.error("Failed to authenticate with cloud. Please verify credentials in config.json.")
        sys.exit(1)

    if not cloud.fetch_device_info():
        log.error("Could not retrieve device information.")
        sys.exit(1)

    # Initial wake command
    cloud.wake_device()

    # In-memory switch states
    current_switches = {
        "ac_switch": False,
        "dc_switch": False,
        "usb_switch": False
    }

    # --- MQTT ---
    mqtt_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    mqtt_client.will_set(AVAILABILITY_TOPIC, payload="offline", qos=1, retain=True)

    COMMAND_TOPICS = {
        f"{TOPIC_BASE}/ac_switch/set":  "ac_switch",
        f"{TOPIC_BASE}/dc_switch/set":  "dc_switch",
        f"{TOPIC_BASE}/usb_switch/set": "usb_switch",
    }

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            log.info("✅ Connected to MQTT broker at %s:%s", MQTT_BROKER, MQTT_PORT)
            configure_ha_discovery(client)
            for topic in COMMAND_TOPICS:
                client.subscribe(topic)
                log.info("📥 Subscribed to command topic: %s", topic)
        else:
            log.error("MQTT connection error: code %s", rc)

    def on_message(client, userdata, msg):
        """Handles ON/OFF commands received via MQTT."""
        topic   = msg.topic
        payload = msg.payload.decode(errors="replace").strip().upper()
        prop    = COMMAND_TOPICS.get(topic)
        if not prop:
            return

        log.info("📥 [Command] %s → %s", prop, payload)
        if payload not in ("ON", "OFF"):
            log.warning("Unrecognized payload '%s' on %s, discarding.", payload, topic)
            return

        target_state = (payload == "ON")
        current_state = current_switches.get(prop, False)

        if current_state == target_state:
            log.info("ℹ️ Switch %s is already %s.", prop, payload)
            client.publish(f"{TOPIC_BASE}/{prop}", payload, retain=False)
            return

        # Send control command directly to Cloud
        success = cloud.control_device([{prop: target_state}])
        if success:
            current_switches[prop] = target_state
            client.publish(f"{TOPIC_BASE}/{prop}", payload, retain=False)
            log.info("✅ Physical state of %s updated to %s", prop, payload)
        else:
            log.error("❌ Failed to toggle switch %s", prop)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        log.error("Could not connect to MQTT broker: %s", e)
        sys.exit(1)

    log.info("Starting telemetry polling every %s seconds.", INTERVALO_SEGUNDOS)
    offline_streak = 0
    last_wake_time = time.time()

    while True:
        try:
            # Keep-alive periódico para que la batería NUNCA se duerma
            if time.time() - last_wake_time >= INTERVALO_WAKE_SEGUNDOS:
                cloud.wake_device()
                last_wake_time = time.time()

            metrics = cloud.get_telemetry()

            if metrics and metrics.get("online", False):
                # ---- DISPOSITIVO ONLINE ----
                if offline_streak > 0:
                    log.info("✅ Oukitel station back online (after %s offline cycles).", offline_streak)
                offline_streak = 0
                mqtt_client.publish(AVAILABILITY_TOPIC, "online", retain=True)

                battery         = metrics.get("battery_percentage", 0)
                input_total     = metrics.get("total_input_power", 0)
                output_total    = metrics.get("total_output_power", 0)
                ac_input        = metrics.get("ac_input", 0)
                dc_input        = metrics.get("dc_input", 0)
                temp            = metrics.get("temp", 0)
                remain_time     = metrics.get("remain_time", 0)
                remain_chg_time = metrics.get("remain_charging_time", 0)
                ac_limit        = metrics.get("ac_charging_limit", 100)
                wifi_signal     = metrics.get("wifi_signal", 0)

                ac_bool  = bool(metrics.get("ac_switch", False))
                dc_bool  = bool(metrics.get("dc_switch", False))
                usb_bool = bool(metrics.get("usb_switch", False))

                current_switches["ac_switch"]  = ac_bool
                current_switches["dc_switch"]  = dc_bool
                current_switches["usb_switch"] = usb_bool

                ac_sw  = "ON" if ac_bool else "OFF"
                dc_sw  = "ON" if dc_bool else "OFF"
                usb_sw = "ON" if usb_bool else "OFF"

                mqtt_client.publish(f"{TOPIC_BASE}/battery",              battery,         retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/input_watts",          input_total,     retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/output_watts",         output_total,    retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/temperature",          temp,            retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/ac_input_watts",       ac_input,        retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/dc_input_watts",       dc_input,        retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/remain_time",          remain_time,     retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/remain_charging_time", remain_chg_time, retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/ac_charging_limit",    ac_limit,        retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/wifi_signal",          wifi_signal,     retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/ac_switch",            ac_sw,           retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/dc_switch",            dc_sw,           retain=False)
                mqtt_client.publish(f"{TOPIC_BASE}/usb_switch",           usb_sw,          retain=False)

                log.info(
                    "📊 Bat: %s%% | In: %sW (AC:%sW DC:%sW) | Out: %sW | Temp: %s°C "
                    "| Remaining: %smin | AC:%s DC:%s USB:%s",
                    battery, input_total, ac_input, dc_input, output_total, temp,
                    remain_time, ac_sw, dc_sw, usb_sw
                )

            else:
                # ---- DEVICE OFFLINE / SLEEP ----
                offline_streak += 1

                if offline_streak == 1:
                    log.warning("⚠️ Device offline or sleeping. Sending wake-up command...")
                    cloud.wake_device()
                    last_wake_time = time.time()
                elif offline_streak % 2 == 0:
                    log.warning("⚠️ Device sleeping (cycle #%s). Resending wake-up keep-alive...", offline_streak)
                    cloud.wake_device()
                    last_wake_time = time.time()

        except Exception as e:
            log.error("Exception in polling loop: %s", e)

        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
