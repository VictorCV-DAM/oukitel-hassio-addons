"""
================================================================================
OUKITEL P2001 CLOUD API -> HOME ASSISTANT MQTT BRIDGE (V4 Cloud API - Autónomo)
================================================================================
- Conexión 100% nativa y directa a la API Cloud de Acceleronix / Wonderfree.
- CERO dependencia de smartphone o ADB: totalmente autónomo desde el MiniPC.
- Control bidireccional de interruptores: AC, DC 12V y USB directamente desde Home Assistant.
- Despertador / Keep-Alive autónomo continuo mediante high_frequency_reporting (LAN+WiFi).
- Telemetría en tiempo real: batería, entrada total/AC/DC, salida, temperatura, tiempos.
- Renovación automática y transparente de token de sesión OAuth.
- Descubrimiento automático en Home Assistant (MQTT Discovery para sensores y switches).
- 100% autocontenido y portable en la carpeta V4.
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

# Configuraciones de regiones de servidores disponibles
# [NOTA]: 'EU' es la región principal probada y verificada en hardware real.
# 'US' y 'CN' se incluyen de forma [EXPERIMENTAL] a partir de los endpoints descubiertos en el APK oficial.
REGION_SERVERS = {
    "EU": {
        "name": "Europa (Verificado)",
        "base_url": "https://iot-api.acceleronix.io",
        "user_domain": "E.SP.4294967410",
        "user_domain_secret": "3aRNUwWahjyANa7WfBK2wCCkxCexB6nXxKJwXxfePvzf",
        "appid": "277",
        "appversion": "3.7.5"
    },
    "US": {
        "name": "Norteamérica / USA [EXPERIMENTAL]",
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
        "email": "victigre3000@gmail.com",
        "password": "GqUBR@W1PIOWvU",
        "base_url": REGION_SERVERS["EU"]["base_url"],
        "user_domain": REGION_SERVERS["EU"]["user_domain"],
        "user_domain_secret": REGION_SERVERS["EU"]["user_domain_secret"],
        "appid": REGION_SERVERS["EU"]["appid"],
        "appversion": REGION_SERVERS["EU"]["appversion"]
    },
    "mqtt": {
        "broker": "192.168.1.219",
        "port": 1883,
        "user": "victorlike",
        "password": "kzW3kEw&F8cCbZ",
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
        """Autenticación en la nube de Acceleronix con renovación automática."""
        log.info("Iniciando sesión en la nube de Acceleronix...")
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
                log.info("✅ Sesión iniciada con éxito. Token válido hasta: %s (auto-renovable)",
                         time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.token_expiry)))
                return True
            else:
                log.error("❌ Error de inicio de sesión: %s (código %s)", res.get("msg"), res.get("code"))
                return False
        except Exception as e:
            log.error("⚠️ Excepción durante el login: %s", e)
            return False

    def ensure_authenticated(self):
        """Asegura que el token sea válido; si está próximo a expirar (120s antes) o no existe, renueva solo."""
        if not self.access_token or time.time() > (self.token_expiry - 120):
            log.info("🔄 Token próximo a caducar o nulo. Renovando automáticamente...")
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
        """Obtiene el identificador del dispositivo (deviceKey y productKey)."""
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
                    log.info("🔋 Dispositivo detectado: %s (DK: %s, PK: %s)",
                             self.device_name, self.device_key, self.product_key)
                    return True
                else:
                    log.error("❌ No se encontraron dispositivos vinculados a esta cuenta.")
                    return False
            elif res.get("code") == 5032:
                log.warning("Token expirado, reintentando login...")
                self.login()
                return self.fetch_device_info()
            else:
                log.error("Error al obtener lista de dispositivos: %s", res.get("msg"))
                return False
        except Exception as e:
            log.error("Excepción en fetch_device_info: %s", e)
            return False

    def control_device(self, properties_list: list) -> bool:
        """
        Envía comandos de control físico a la batería mediante la API REST oficial de Quectel/Acceleronix.
        properties_list: Lista de diccionarios, ej. [{"ac_switch": True}], [{"high_frequency_reporting": 3}]
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
                log.info("⚡ [Cloud Command] Éxito enviando %s (Ticket: %s)", properties_list, tickets)
                return True
            elif code == 5032:
                log.warning("Token expirado en control_device, renovando sesión...")
                self.login()
                return self.control_device(properties_list)
            else:
                log.error("❌ Error de control en Cloud: %s (código %s)", res.get("msg"), code)
                return False
        except Exception as e:
            log.error("⚠️ Excepción al enviar comando de control: %s", e)
            return False

    def wake_device(self) -> bool:
        """
        Despierta y mantiene activa la batería solicitando reporte de alta frecuencia (Wi-Fi + LAN).
        high_frequency_reporting = 3
        """
        log.info("🔔 [Keep-Alive] Enviando orden de reporte de alta frecuencia a la batería...")
        return self.control_device([{"high_frequency_reporting": 3}])

    def get_telemetry(self) -> dict:
        """Consulta la telemetría en tiempo real desde la API Cloud."""
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
                log.warning("Token caducado en consulta. Renovando sesión...")
                self.login()
                return self.get_telemetry()

            if res.get("code") != 200:
                log.warning("Respuesta no OK de telemetría: %s", res)
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
# --- MQTT Y HOME ASSISTANT DISCOVERY ---
# ==========================================
def configurar_descubrimiento_ha(client: mqtt.Client):
    """Publica la configuración de Home Assistant MQTT Discovery para sensores y switches."""
    device_info = {
        "identifiers": ["oukitel_p2001_station"],
        "name": "Oukitel P2001",
        "model": "P2001 Plus",
        "manufacturer": "OUKITEL",
        "sw_version": "V4 Cloud API Autónomo"
    }

    # Sensores de telemetría (lectura)
    sensores = [
        ("battery",              "Batería Oukitel",           "%",   "battery",         "measurement", "mdi:battery-charging"),
        ("input_watts",          "Entrada Total",             "W",   "power",           "measurement", "mdi:solar-power"),
        ("output_watts",         "Salida Total",              "W",   "power",           "measurement", "mdi:flash"),
        ("temperature",          "Temperatura Oukitel",       "°C",  "temperature",     "measurement", "mdi:thermometer"),
        ("ac_input_watts",       "Entrada AC",                "W",   "power",           "measurement", "mdi:transmission-tower"),
        ("dc_input_watts",       "Entrada DC (Solar)",        "W",   "power",           "measurement", "mdi:solar-panel"),
        ("remain_time",          "Tiempo Restante Descarga",  "min", "duration",        "measurement", "mdi:timer-outline"),
        ("remain_charging_time", "Tiempo Restante Carga",     "min", "duration",        "measurement", "mdi:timer-sand"),
        ("ac_charging_limit",    "Límite Carga AC",           "%",   None,              "measurement", "mdi:gauge"),
        ("wifi_signal",          "Señal WiFi Oukitel",        "dBm", "signal_strength", "measurement", "mdi:wifi"),
    ]

    for id_sensor, nombre, unidad, clase, state_cl, icono in sensores:
        config_topic = f"homeassistant/sensor/oukitel_{id_sensor}/config"
        payload = {
            "name": nombre,
            "state_topic": f"{TOPIC_BASE}/{id_sensor}",
            "unique_id": f"oukitel_p2001_{id_sensor}",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info,
        }
        if unidad:
            payload["unit_of_measurement"] = unidad
        if clase:
            payload["device_class"] = clase
        if state_cl:
            payload["state_class"] = state_cl
        if icono:
            payload["icon"] = icono
        client.publish(config_topic, json.dumps(payload), retain=True)

    # Switches (accionadores interactivos bidireccionales)
    switches = [
        ("ac_switch",  "Interruptor AC",     "mdi:power-socket-eu"),
        ("dc_switch",  "Interruptor DC 12V", "mdi:car-electric"),
        ("usb_switch", "Interruptor USB",    "mdi:usb-port"),
    ]
    for id_sw, nombre, icono in switches:
        config_topic = f"homeassistant/switch/oukitel_{id_sw}/config"
        payload = {
            "name": nombre,
            "state_topic":   f"{TOPIC_BASE}/{id_sw}",
            "command_topic": f"{TOPIC_BASE}/{id_sw}/set",
            "payload_on":    "ON",
            "payload_off":   "OFF",
            "state_on":      "ON",
            "state_off":     "OFF",
            "icon": icono,
            "unique_id": f"oukitel_p2001_{id_sw}",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available":     "online",
            "payload_not_available": "offline",
            "device": device_info,
            "optimistic": False,
        }
        client.publish(config_topic, json.dumps(payload), retain=True)

    log.info("✅ Entidades registradas en Home Assistant MQTT Discovery.")


# ==========================================
# --- BUCLE PRINCIPAL ---
# ==========================================
def main():
    log.info("Iniciando Oukitel Cloud MQTT Bridge V4 (100% Autónomo - Sin Móvil/ADB)...")
    log.info("Configuración: %s", CONFIG_FILE)
    if SELECTED_REGION in REGION_SERVERS:
        reg_info = REGION_SERVERS[SELECTED_REGION]
        log.info("🌍 Región Cloud seleccionada: %s - %s (%s)", SELECTED_REGION, reg_info["name"], BASE_URL)
        if SELECTED_REGION != "EU":
            log.warning("⚠️ [AVISO EXPERIMENTAL]: La región '%s' se basa en los endpoints de la app oficial pero no ha sido verificada con hardware físico.", SELECTED_REGION)
    else:
        log.info("🌍 Región Cloud seleccionada: PERSONALIZADA (%s)", BASE_URL)

    cloud = AcceleronixCloudClient(CLOUD_EMAIL, CLOUD_PASSWORD)
    if not cloud.login():
        log.error("No se pudo iniciar sesión en la nube. Revisa las credenciales en config.json.")
        sys.exit(1)

    if not cloud.fetch_device_info():
        log.error("No se pudo obtener información del dispositivo.")
        sys.exit(1)

    # Enviar un wake inicial para despertar la batería
    cloud.wake_device()

    # Estado actual en memoria para los switches
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
            log.info("✅ Conectado al broker MQTT en %s:%s", MQTT_BROKER, MQTT_PORT)
            configurar_descubrimiento_ha(client)
            for topic in COMMAND_TOPICS:
                client.subscribe(topic)
                log.info("📥 Suscrito a topic de comando: %s", topic)
        else:
            log.error("Error de conexión MQTT: código %s", rc)

    def on_message(client, userdata, msg):
        """Gestiona comandos ON/OFF emitidos desde Home Assistant hacia la batería vía Cloud API."""
        topic   = msg.topic
        payload = msg.payload.decode(errors="replace").strip().upper()
        prop    = COMMAND_TOPICS.get(topic)
        if not prop:
            return

        log.info("📥 [HA Command] %s → %s", prop, payload)
        if payload not in ("ON", "OFF"):
            log.warning("Payload no reconocido '%s' en %s, descartado.", payload, topic)
            return

        target_state = (payload == "ON")
        current_state = current_switches.get(prop, False)

        if current_state == target_state:
            log.info("ℹ️ Switch %s ya se encuentra en %s.", prop, payload)
            client.publish(f"{TOPIC_BASE}/{prop}", payload, retain=False)
            return

        # Enviar comando de control directamente a la Cloud
        success = cloud.control_device([{prop: target_state}])
        if success:
            current_switches[prop] = target_state
            client.publish(f"{TOPIC_BASE}/{prop}", payload, retain=False)
            log.info("✅ Estado físico de %s actualizado a %s en Home Assistant", prop, payload)
        else:
            log.error("❌ Falló la conmutación de %s", prop)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        log.error("No se pudo conectar al broker MQTT: %s", e)
        sys.exit(1)

    log.info("Iniciando sondeo de telemetría cada %s segundos.", INTERVALO_SEGUNDOS)
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
                    log.info("✅ Dispositivo Oukitel en línea (tras %s ciclos offline).", offline_streak)
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
                    "| Restante: %smin | AC:%s DC:%s USB:%s",
                    battery, input_total, ac_input, dc_input, output_total, temp,
                    remain_time, ac_sw, dc_sw, usb_sw
                )

            else:
                # ---- DISPOSITIVO OFFLINE / SLEEP ----
                offline_streak += 1

                if offline_streak == 1:
                    log.warning("⚠️ Dispositivo offline o en reposo. Intentando despertar...")
                    cloud.wake_device()
                    last_wake_time = time.time()
                elif offline_streak % 2 == 0:
                    log.warning("⚠️ Dispositivo en reposo (ciclo #%s). Reenviando orden de despertar...", offline_streak)
                    cloud.wake_device()
                    last_wake_time = time.time()

        except Exception as e:
            log.error("Excepción en el ciclo de lectura: %s", e)

        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
