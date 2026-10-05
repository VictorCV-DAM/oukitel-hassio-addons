"""
================================================================================
OUKITEL POWER STATION -> HOME ASSISTANT MQTT BRIDGE (Standalone Daemon)
================================================================================
- Dual transport: local LAN (TCP 6607, AES-128 push) with Cloud API fallback.
- Connection modes: auto (LAN preferred + Cloud), lan (local only), cloud.
- UDP broadcast discovery of the station on the local network (port 6606).
- Bidirectional controls: AC 230V, DC 12V and USB outputs, AC charging limit,
  output frequency and output voltage.
- Autonomous keep-alive (LAN heartbeat / Cloud high_frequency_reporting).
- Real-time telemetry: battery SOC, total/AC/DC input power, output power,
  temperature, remaining times, firmware versions.
- authKey cache: once fetched, LAN mode survives restarts without internet.
- Home Assistant MQTT Auto-Discovery for all entities.
- Standalone and portable across Windows, Linux, and macOS.
================================================================================
"""

import hashlib
import json
import logging
import os
import random
import socket
import string
import struct
import sys
import threading
import time
from base64 import b64decode, b64encode

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

BRIDGE_VERSION = "1.3.0"

FAULT_STATUS_OPTIONS = [
    "Normal",
    "High Temperature Warning",
    "Over-Temperature",
    "Under-Temperature",
    "Low Battery Warning",
    "Critical Low Battery",
    "Overload Protection",
    "Hardware Fault",
]

# Base path (supports frozen executables)
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
ICON_FILE = os.path.join(BASE_DIR, "icon.ico")
DATA_DIR = os.environ.get("OUKITEL_DATA_DIR", BASE_DIR)
CACHE_FILE = os.path.join(DATA_DIR, ".oukitel_cache.json")


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

MODE_AUTO = "auto"
MODE_LAN = "lan"
MODE_CLOUD = "cloud"
CONNECTION_MODES = (MODE_AUTO, MODE_LAN, MODE_CLOUD)

# Default values
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
    "connection": {
        "mode": MODE_AUTO,
        "lan_host": ""
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

# Load configuration from config.json
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"Error reading {CONFIG_FILE}, using defaults: {e}")
        cfg = DEFAULT_CONFIG
else:
    cfg = DEFAULT_CONFIG

cloud_cfg = cfg.get("cloud", {})
SELECTED_REGION = str(cloud_cfg.get("region", "EU")).upper()

# Region resolution (EU / US / CN / CUSTOM)
if SELECTED_REGION in REGION_SERVERS:
    reg_data = REGION_SERVERS[SELECTED_REGION]
    BASE_URL           = reg_data["base_url"]
    USER_DOMAIN        = reg_data["user_domain"]
    USER_DOMAIN_SECRET = reg_data["user_domain_secret"]
    APP_ID             = reg_data["appid"]
    APP_VERSION        = reg_data["appversion"]
else:
    # Custom region or manual values in config.json
    SELECTED_REGION = "CUSTOM"
    BASE_URL           = cloud_cfg.get("base_url")           or DEFAULT_CONFIG["cloud"]["base_url"]
    USER_DOMAIN        = cloud_cfg.get("user_domain")        or DEFAULT_CONFIG["cloud"]["user_domain"]
    USER_DOMAIN_SECRET = cloud_cfg.get("user_domain_secret") or DEFAULT_CONFIG["cloud"]["user_domain_secret"]
    APP_ID             = cloud_cfg.get("appid")              or DEFAULT_CONFIG["cloud"]["appid"]
    APP_VERSION        = cloud_cfg.get("appversion")         or DEFAULT_CONFIG["cloud"]["appversion"]

CLOUD_EMAIL    = cloud_cfg.get("email",    DEFAULT_CONFIG["cloud"]["email"])
CLOUD_PASSWORD = cloud_cfg.get("password", DEFAULT_CONFIG["cloud"]["password"])

conn_cfg = cfg.get("connection", {})
CONNECTION_MODE = str(conn_cfg.get("mode") or cfg.get("connection_mode") or MODE_AUTO).strip().lower()
if CONNECTION_MODE not in CONNECTION_MODES:
    CONNECTION_MODE = MODE_AUTO
LAN_HOST_STATIC = str(conn_cfg.get("lan_host") or cfg.get("lan_host") or "").strip()

MQTT_BROKER  = cfg.get("mqtt", {}).get("broker",     DEFAULT_CONFIG["mqtt"]["broker"])
MQTT_PORT    = int(cfg.get("mqtt", {}).get("port",   DEFAULT_CONFIG["mqtt"]["port"]))
MQTT_USER    = cfg.get("mqtt", {}).get("user",       DEFAULT_CONFIG["mqtt"]["user"])
MQTT_PASS    = cfg.get("mqtt", {}).get("password",   DEFAULT_CONFIG["mqtt"]["password"])
TOPIC_BASE   = cfg.get("mqtt", {}).get("topic_base", DEFAULT_CONFIG["mqtt"]["topic_base"])
AVAILABILITY_TOPIC = f"{TOPIC_BASE}/availability"

POLL_INTERVAL = int(cfg.get("polling", {}).get("interval_seconds", DEFAULT_CONFIG["polling"]["interval_seconds"]))
WAKE_INTERVAL = int(cfg.get("polling", {}).get("wake_interval_seconds", DEFAULT_CONFIG["polling"]["wake_interval_seconds"]))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("OukitelBridge")


# ==========================================
# --- LOCAL CACHE (authKey / deviceKey) ---
# ==========================================
def load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log.warning("⚠️ Could not write cache file %s: %s", CACHE_FILE, e)


# ==========================================
# --- ACCELERONIX CLOUD CLIENT ---
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
        self.auth_key = None

    def _encrypt_password(self, password: str, random_str: str) -> str:
        """AES-128-CBC encryption required by the Quectel/Acceleronix login protocol."""
        md5_hash = hashlib.md5(random_str.encode('utf-8')).hexdigest().upper()
        aes_key = md5_hash[8:24].encode('utf-8')
        iv = (md5_hash[16:24] + md5_hash[8:16]).encode('utf-8')
        cipher = AES.new(aes_key, AES.MODE_CBC, iv)
        padded = pad(password.encode('utf-8'), 16)
        return b64encode(cipher.encrypt(padded)).decode('utf-8')

    def _calculate_signature(self, email: str, pwd_b64: str, random_str: str) -> str:
        """SHA-256 login signature."""
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
        """Fetches device identifiers (deviceKey, productKey) and the local authKey."""
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
                    self.auth_key = dev.get("authKey")
                    log.info("🔋 Power station detected: %s (DK: %s, PK: %s, authKey: %s)",
                             self.device_name, self.device_key, self.product_key,
                             "present" if self.auth_key else "missing")
                    return True
                log.error("❌ No devices found registered in this account.")
                return False
            elif res.get("code") == 5032:
                log.warning("Token expired, retrying login...")
                self.login()
                return self.fetch_device_info()
            log.error("Error retrieving device list: %s", res.get("msg"))
            return False
        except Exception as e:
            log.error("Exception in fetch_device_info: %s", e)
            return False

    def control_device(self, properties_list: list) -> bool:
        """
        Sends hardware control commands via the official Acceleronix REST API.
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
            log.error("❌ Cloud control error: %s (code %s)", res.get("msg"), code)
            return False
        except Exception as e:
            log.error("⚠️ Exception sending control command: %s", e)
            return False

    def wake_device(self) -> bool:
        """Keeps the station reporting by requesting high-frequency reporting (Wi-Fi + LAN)."""
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
            log.error("Exception while querying telemetry: %s", e)
            return {}


# ==========================================
# --- LOCAL LAN PROTOCOL (UDP 6606 / TCP 6607) ---
# ==========================================
LAN_UDP_PORT = 6606
LAN_TCP_PORT = 6607

CMD_SCAN_REQUEST = 28720
CMD_SCAN_REPLY   = 28721
CMD_HELLO        = 28722
CMD_NONCE        = 28723
CMD_LOGIN        = 28724
CMD_LOGIN_RESULT = 28725
CMD_WRITE_ACK    = 28726
CMD_PING         = 28727
CMD_PONG         = 28728
CMD_HEARTBEAT    = 28729
CMD_READ         = 17
CMD_WRITE        = 19
CMD_REPORT       = 20

TAG_HF_REPORTING      = 100
HF_REPORTING_LAN_WIFI = 3
LAN_READ_TAGS = (2, 8, 9, 6, 31, 7, 28, 27, 14, 12, 11, 5, 4, 3, 1, 34, 20, 100, 43, 44, 46)

LAN_CONNECT_TIMEOUT    = 15.0
LAN_READ_TIMEOUT       = 90.0
LAN_REARM_INTERVAL     = 12.0
LAN_STALE_TIMEOUT      = 150.0
LAN_RETRY_DELAY        = 30.0
LAN_PUBLISH_MIN_PERIOD = 1.0
CLOUD_SNAPSHOT_PERIOD  = 300.0

_FRAME_MAGIC = b"\xaa\xaa"


class LanError(Exception):
    pass


def lan_aes_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plaintext, 16))


def lan_aes_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    data = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
    n = data[-1] if data else 0
    if 1 <= n <= 16 and data[-n:] == bytes([n]) * n:
        return data[:-n]
    return data


def lan_session_token(key: bytes, nonce: str) -> str:
    return hashlib.sha256((key.hex() + ";" + nonce).encode()).hexdigest()


def _stuff(frame: bytes) -> bytes:
    out = bytearray(frame[:2])
    body = frame[2:]
    for j, b in enumerate(body):
        out.append(b)
        if b == 0xAA and j + 1 < len(body) and body[j + 1] in (0x55, 0xAA):
            out.append(0x55)
    return bytes(out)


class _Destuffer:
    def __init__(self) -> None:
        self._saw_aa = False

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        saw_aa = self._saw_aa
        for b in data:
            if saw_aa and b == 0x55:
                saw_aa = False
                continue
            out.append(b)
            saw_aa = b == 0xAA
        self._saw_aa = saw_aa
        return bytes(out)


def build_frame(packet_id: int, cmd: int, payload: bytes = b"") -> bytes:
    inner = struct.pack(">HH", packet_id & 0xFFFF, cmd & 0xFFFF) + payload
    raw = _FRAME_MAGIC + struct.pack(">HB", len(inner) + 1, sum(inner) & 0xFF) + inner
    return _stuff(raw)


class FrameAssembler:
    """Reassembles variable-length frames from a raw byte stream."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._destuffer = _Destuffer()

    def feed(self, data: bytes) -> list:
        self._buf.extend(self._destuffer.feed(data))
        frames = []
        while True:
            k = self._buf.find(_FRAME_MAGIC)
            if k < 0:
                if len(self._buf) > 1:
                    del self._buf[:-1]
                break
            if k > 0:
                del self._buf[:k]
            if len(self._buf) < 9:
                break
            body_len = struct.unpack_from(">H", self._buf, 2)[0]
            total = 4 + body_len
            if len(self._buf) < total:
                break
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            if (sum(frame[5:]) & 0xFF) != frame[4]:
                continue
            pid = struct.unpack_from(">H", frame, 5)[0]
            cmd = struct.unpack_from(">H", frame, 7)[0]
            frames.append((pid, cmd, frame[9:]))
        return frames


def _ttlv_encode_int(value: int) -> bytes:
    neg = value < 0
    v = abs(value)
    body = b"\x00" if v == 0 else v.to_bytes((v.bit_length() + 7) // 8, "big")
    return bytes([(0x80 if neg else 0) | ((len(body) - 1) & 0x07)]) + body


def ttlv_encode(fields: list) -> bytes:
    out = bytearray()
    for tag, kind, value in fields:
        if kind == "bool":
            out += struct.pack(">H", (tag << 3) | (1 if value else 0))
        elif kind == "num":
            out += struct.pack(">H", (tag << 3) | 2)
            out += _ttlv_encode_int(int(value))
        else:
            raise ValueError(f"unknown TTLV kind: {kind}")
    return bytes(out)


def ttlv_decode(buf: bytes) -> dict:
    out = {}
    i = 0
    n = len(buf)

    def read_num(pos):
        ctrl = buf[pos]
        pos += 1
        sign = (ctrl >> 7) & 1
        decimals = (ctrl >> 3) & 0x0F
        nbytes = (ctrl & 0x07) + 1
        v = int.from_bytes(buf[pos:pos + nbytes], "big")
        pos += nbytes
        if sign:
            v = -v
        return (v / (10 ** decimals) if decimals else v), pos

    while i + 2 <= n:
        h = struct.unpack_from(">H", buf, i)[0]
        i += 2
        tag, typ = (h >> 3) & 0x1FFF, h & 7
        if typ in (0, 1):
            out[tag] = typ == 1
        elif typ == 2:
            out[tag], i = read_num(i)
        elif typ in (3, 5):
            if i + 2 > n:
                break
            ln = struct.unpack_from(">H", buf, i)[0]
            i += 2
            val = buf[i:i + ln]
            i += ln
            out[tag] = val.decode("ascii") if val.isascii() else val
        elif typ == 4:
            if i + 2 > n:
                break
            count = struct.unpack_from(">H", buf, i)[0]
            i += 2
            sub = {}
            for _ in range(count):
                if i + 2 > n:
                    break
                h2 = struct.unpack_from(">H", buf, i)[0]
                i += 2
                t2, ty2 = (h2 >> 3) & 0x1FFF, h2 & 7
                if ty2 in (0, 1):
                    sub[t2] = ty2 == 1
                elif ty2 == 2:
                    sub[t2], i = read_num(i)
                elif ty2 in (3, 5):
                    ln = struct.unpack_from(">H", buf, i)[0]
                    i += 2
                    sub[t2] = buf[i:i + ln]
                    i += ln
            out[tag] = sub
        else:
            break
    return out


def _broadcast_targets() -> list:
    targets = ["255.255.255.255"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        local_ip = s.getsockname()[0]
        s.close()
        directed = local_ip.rsplit(".", 1)[0] + ".255"
        if directed not in targets:
            targets.append(directed)
    except OSError:
        pass
    return targets


def _parse_scan_reply(payload: bytes):
    ip = mac = None
    for value in ttlv_decode(payload).values():
        if not isinstance(value, str):
            continue
        if value.count(".") == 3 and all(p.isdigit() for p in value.split(".")):
            ip = value
        elif len(value) == 12 and all(c in "0123456789abcdefABCDEF" for c in value):
            mac = value.lower()
    return ip, mac


def lan_discover(device_key: str, timeout: float = 6.0):
    """Broadcasts a UDP probe and returns the IP of the station whose MAC == device_key."""
    dk = (device_key or "").lower()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        sock.settimeout(0.5)
        probe = build_frame(1000, CMD_SCAN_REQUEST)
        targets = _broadcast_targets()
        log.info("📡 [LAN] Scanning for station %s on %s ...", dk, targets)
        for _ in range(3):
            for target in targets:
                try:
                    sock.sendto(probe, (target, LAN_UDP_PORT))
                except OSError as e:
                    log.debug("UDP send to %s failed: %s", target, e)
            end = time.monotonic() + timeout / 3
            while time.monotonic() < end:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                for _pid, cmd, payload in FrameAssembler().feed(data):
                    if cmd != CMD_SCAN_REPLY:
                        continue
                    ip, mac = _parse_scan_reply(payload)
                    if mac and mac == dk:
                        return ip or addr[0]
        return None
    except OSError as e:
        log.warning("⚠️ [LAN] Discovery socket error: %s", e)
        return None
    finally:
        sock.close()


class LanLink:
    """One TCP session with the station: handshake -> subscribe -> push stream + keep-alive."""

    def __init__(self, host: str, auth_key_b64: str, on_report):
        self.host = host
        self._key = b64decode(auth_key_b64)
        self._on_report = on_report
        self._sock = None
        self._iv = None
        self._packet_id = 1000
        self._send_lock = threading.Lock()
        self._assembler = FrameAssembler()
        self._stop = threading.Event()
        self.connected = False
        self.last_report = 0.0

    def _next_pid(self) -> int:
        self._packet_id += 1
        if self._packet_id >= 0xFFFF:
            self._packet_id = 1000
        return self._packet_id

    def _send(self, cmd: int, payload: bytes = b"", encrypt: bool = False) -> None:
        if encrypt:
            payload = lan_aes_encrypt(self._key, self._iv, payload)
        with self._send_lock:
            self._sock.sendall(build_frame(self._next_pid(), cmd, payload))

    def _recv_cmd(self, expected: int, timeout: float = 10.0) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LanError(f"timeout waiting for cmd {expected}")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                raise LanError(f"timeout waiting for cmd {expected}")
            if not chunk:
                raise LanError("connection closed during handshake")
            for _pid, cmd, payload in self._assembler.feed(chunk):
                if cmd == expected:
                    return payload

    def connect(self) -> None:
        try:
            self._sock = socket.create_connection((self.host, LAN_TCP_PORT), timeout=LAN_CONNECT_TIMEOUT)
        except OSError as e:
            raise LanError(f"cannot reach {self.host}:{LAN_TCP_PORT}: {e}")
        try:
            self._handshake()
            self._rearm()
        except Exception:
            self.close()
            raise
        self.connected = True
        threading.Thread(target=self._reader, name="lan-reader", daemon=True).start()
        threading.Thread(target=self._keepalive, name="lan-keepalive", daemon=True).start()

    def _handshake(self) -> None:
        self._send(CMD_HELLO)
        nonce_fields = ttlv_decode(self._recv_cmd(CMD_NONCE))
        nonce = next((v for v in nonce_fields.values() if isinstance(v, str)), None)
        if not nonce:
            raise LanError("station did not send a nonce")
        self._iv = nonce.encode()

        token = lan_session_token(self._key, nonce).encode()
        login_body = struct.pack(">H", (2 << 3) | 3) + struct.pack(">H", len(token)) + token
        self._send(CMD_LOGIN, login_body)

        result_fields = ttlv_decode(self._recv_cmd(CMD_LOGIN_RESULT))
        result = next((v for v in result_fields.values() if isinstance(v, (int, float))), None)
        if result != 0:
            raise LanError(f"station rejected login (result={result})")

    def _rearm(self) -> None:
        """Re-asserts high-frequency reporting, requests a snapshot and sends a heartbeat."""
        self._send(CMD_WRITE, ttlv_encode([(TAG_HF_REPORTING, "num", HF_REPORTING_LAN_WIFI)]), encrypt=True)
        self._send(CMD_READ, b"".join(struct.pack(">H", t) for t in LAN_READ_TAGS), encrypt=True)
        self._send(CMD_HEARTBEAT, ttlv_encode([(1, "num", 30), (2, "num", 1)]), encrypt=True)

    def _reader(self) -> None:
        try:
            self._sock.settimeout(LAN_READ_TIMEOUT)
            while not self._stop.is_set():
                try:
                    chunk = self._sock.recv(4096)
                except socket.timeout:
                    log.warning("⚠️ [LAN] No data for %ss, dropping session.", int(LAN_READ_TIMEOUT))
                    break
                except OSError:
                    break
                if not chunk:
                    log.warning("⚠️ [LAN] Socket closed by station.")
                    break
                for _pid, cmd, payload in self._assembler.feed(chunk):
                    self._dispatch(cmd, payload)
        finally:
            self.connected = False
            self.close()

    def _dispatch(self, cmd: int, payload: bytes) -> None:
        if cmd in (CMD_REPORT, CMD_READ) and payload and self._iv:
            try:
                fields = ttlv_decode(lan_aes_decrypt(self._key, self._iv, payload))
            except Exception as e:
                log.debug("Could not decode LAN frame cmd=%s: %s", cmd, e)
                return
            if fields:
                self.last_report = time.monotonic()
                self._on_report(fields)
        elif cmd == CMD_PING:
            try:
                self._send(CMD_PONG)
            except OSError:
                pass

    def _keepalive(self) -> None:
        while not self._stop.wait(LAN_REARM_INTERVAL):
            try:
                self._rearm()
            except Exception as e:
                log.warning("⚠️ [LAN] Keep-alive failed (%s), closing session.", e)
                self.close()
                return

    def write(self, tag: int, kind: str, value) -> None:
        if not self.connected:
            raise LanError("LAN session not connected")
        self._send(CMD_WRITE, ttlv_encode([(tag, kind, value)]), encrypt=True)

    def close(self) -> None:
        self._stop.set()
        self.connected = False
        sock, self._sock = self._sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


# ==========================================
# --- DATA MODEL / MAPPINGS ---
# ==========================================
# LAN TTLV tag -> bridge key. Tags 14 (temperature) and 28 (output voltage)
# are only reported by the cloud; tag 33 (inverter temperature) covers LAN.
LAN_TAG_MAP = {
    1: "battery",
    2: "remain_time",
    3: "remain_charging_time",
    4: "input_watts",
    5: "output_watts",
    11: "ac_input_watts",
    12: "dc_input_watts",
    14: "temperature",
    20: "ac_charging_limit",
    27: "output_frequency",
    28: "output_voltage",
    31: "inverter_version",
    33: "temperature",
    34: "bms_version",
    43: "ac_switch",
    44: "usb_switch",
    46: "dc_switch",
}

# Cloud resourceCode -> bridge key
CLOUD_KEY_MAP = {
    "battery_percentage": "battery",
    "total_input_power": "input_watts",
    "total_output_power": "output_watts",
    "ac_input": "ac_input_watts",
    "dc_input": "dc_input_watts",
    "temp": "temperature",
    "remain_time": "remain_time",
    "remain_charging_time": "remain_charging_time",
    "ac_charging_limit": "ac_charging_limit",
    "wifi_signal": "wifi_signal",
    "ac_switch": "ac_switch",
    "dc_switch": "dc_switch",
    "usb_switch": "usb_switch",
    "Frequency_Switchover": "output_frequency",
    "ACvoltage_Switchover": "output_voltage",
    "BMS_Version": "bms_version",
    "AC_Version": "inverter_version",
}

# Values the LAN never sends: refreshed from a periodic cloud snapshot in auto mode.
CLOUD_ONLY_KEYS = ("temperature", "output_voltage", "wifi_signal")

FREQUENCY_OPTIONS = ["50Hz", "60Hz"]
VOLTAGE_OPTIONS = ["200V", "208V", "220V", "230V", "240V"]

# Writable entity -> (cloud resourceCode, LAN tag, TTLV kind)
CONTROLS = {
    "ac_switch":         ("ac_switch",            43, "bool"),
    "dc_switch":         ("dc_switch",            46, "bool"),
    "usb_switch":        ("usb_switch",           44, "bool"),
    "ac_charging_limit": ("ac_charging_limit",    20, "num"),
    "output_frequency":  ("Frequency_Switchover", 27, "num"),
    "output_voltage":    ("ACvoltage_Switchover", 28, "num"),
}


def fmt_frequency(value):
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return None
    if v in (50, 60):
        return f"{v}Hz"
    return {0: "50Hz", 1: "60Hz"}.get(v)


def fmt_voltage(value):
    try:
        v = int(float(str(value).upper().replace("V", "").strip()))
    except (TypeError, ValueError):
        return None
    return f"{v}V"


def format_mac(dk: str) -> str:
    if dk and len(dk) == 12:
        return ":".join(dk[i:i + 2] for i in range(0, 12, 2)).upper()
    return dk or ""


def unpack_port_telemetry(source: dict, target: dict) -> None:
    """Extract individual port metrics from LAN tags (6, 7, 8, 9) or Cloud structs."""
    def _parse_dict(val):
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    # 1. AC Info: 2=AC output power (W), 3=AC output voltage (V)
    ac_val = _parse_dict(source.get(6) or source.get("6") or source.get("ac_data") or source.get("AC_Info"))
    if ac_val:
        p = ac_val.get(2) if 2 in ac_val else ac_val.get("2")
        v = ac_val.get(3) if 3 in ac_val else ac_val.get("3")
        if p is not None:
            target["ac_output_power"] = p
        if v is not None:
            target["ac_output_voltage"] = v

    # 2. USB Info: 2=USB-A power (W), 3=USB-C QC power (W)
    usb_val = _parse_dict(source.get(7) or source.get("7") or source.get("usb_data") or source.get("USB_Info"))
    if usb_val:
        usb_a = usb_val.get(2) if 2 in usb_val else usb_val.get("2")
        usb_c = usb_val.get(3) if 3 in usb_val else usb_val.get("3")
        if usb_a is not None:
            target["usb_a_power"] = usb_a
        if usb_c is not None:
            target["usb_c_qc_power"] = usb_c

    # 3. Type-C Info: 2=Type-C 1 (W), 5=Type-C 2 (W), 6=Type-C 3 (W), 7=Type-C 4 (W)
    typec_val = _parse_dict(source.get(8) or source.get("8") or source.get("typec_data") or source.get("TypeC_Info"))
    if typec_val:
        c1 = typec_val.get(2) if 2 in typec_val else typec_val.get("2")
        c2 = typec_val.get(5) if 5 in typec_val else typec_val.get("5")
        c3 = typec_val.get(6) if 6 in typec_val else typec_val.get("6")
        c4 = typec_val.get(7) if 7 in typec_val else typec_val.get("7")
        if c1 is not None:
            target["typec1_power"] = c1
        if c2 is not None:
            target["typec2_power"] = c2
        if c3 is not None:
            target["typec3_power"] = c3
        if c4 is not None:
            target["typec4_power"] = c4

    # 4. DC Info: 2=DC Car output power (W), 3=voltage (V), 4=current (A)
    dc_val = _parse_dict(source.get(9) or source.get("9") or source.get("dc_data") or source.get("DC_Info"))
    if dc_val:
        dc_p = dc_val.get(2) if 2 in dc_val else dc_val.get("2")
        dc_v = dc_val.get(3) if 3 in dc_val else dc_val.get("3")
        dc_a = dc_val.get(4) if 4 in dc_val else dc_val.get("4")
        if dc_p is not None:
            target["dc_output_power"] = dc_p
        if dc_v is not None:
            target["dc_output_voltage"] = dc_v
        if dc_a is not None:
            target["dc_output_current"] = dc_a

    # Defaults for zero unknown states
    for k in (
        "ac_output_power",
        "usb_a_power",
        "usb_c_qc_power",
        "typec1_power",
        "typec2_power",
        "typec3_power",
        "typec4_power",
        "dc_output_power",
    ):
        if k not in target or target[k] is None:
            target[k] = 0


# ==========================================
# --- MQTT AND HOME ASSISTANT DISCOVERY ---
# ==========================================
def configure_ha_discovery(client: mqtt.Client, device_key: str):
    """Publishes Home Assistant MQTT Discovery configuration for all entities."""
    device_info = {
        "identifiers": ["oukitel_p2001_station"],
        "name": "Oukitel P2001",
        "model": "P2001 Plus",
        "manufacturer": "OUKITEL",
        "sw_version": f"MQTT Bridge {BRIDGE_VERSION}",
    }
    mac = format_mac(device_key or "")
    if ":" in mac:
        device_info["connections"] = [["mac", mac]]
        device_info["serial_number"] = mac

    def base(name, uid, state_key):
        return {
            "name": name,
            "state_topic": f"{TOPIC_BASE}/{state_key}",
            "unique_id": f"oukitel_p2001_{uid}",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info,
        }

    # Telemetry sensors (read-only)
    sensors = [
        ("battery",              "Oukitel Battery",          "%",   "battery",         "measurement", "mdi:battery-charging", None),
        ("input_watts",          "Total Input Power",        "W",   "power",           "measurement", "mdi:solar-power",      None),
        ("output_watts",         "Total Output Power",       "W",   "power",           "measurement", "mdi:flash",            None),
        ("temperature",          "Oukitel Temperature",      "°C",  "temperature",     "measurement", "mdi:thermometer",      None),
        ("ac_input_watts",       "AC Input Power",           "W",   "power",           "measurement", "mdi:transmission-tower", None),
        ("dc_input_watts",       "DC Solar Input Power",     "W",   "power",           "measurement", "mdi:solar-panel",      None),
        ("remain_time",          "Remaining Discharge Time", "min", "duration",        "measurement", "mdi:timer-outline",    None),
        ("remain_charging_time", "Remaining Charge Time",    "min", "duration",        "measurement", "mdi:timer-sand",       None),
        ("remaining_time",       "Station LCD Remaining Time", "min", "duration",      "measurement", "mdi:timer",            None),
        ("ac_output_power",      "AC Output Power",          "W",   "power",           "measurement", "mdi:power-socket-eu",  None),
        ("ac_output_voltage",    "AC Output Voltage",        "V",   "voltage",         "measurement", "mdi:sine-wave",        None),
        ("typec1_power",         "Type-C 1 Power",           "W",   "power",           "measurement", "mdi:usb-c",            None),
        ("typec2_power",         "Type-C 2 Power",           "W",   "power",           "measurement", "mdi:usb-c",            None),
        ("typec3_power",         "Type-C 3 Power",           "W",   "power",           "measurement", "mdi:usb-c",            None),
        ("typec4_power",         "Type-C 4 Power",           "W",   "power",           "measurement", "mdi:usb-c",            None),
        ("usb_a_power",          "USB-A Power",              "W",   "power",           "measurement", "mdi:usb-port",         None),
        ("usb_c_qc_power",       "USB-C QC Power",           "W",   "power",           "measurement", "mdi:lightning-bolt",   None),
        ("dc_output_power",      "DC 12V Output Power",      "W",   "power",           "measurement", "mdi:car-electric",     None),
        ("dc_output_voltage",    "DC 12V Output Voltage",    "V",   "voltage",         "measurement", "mdi:sine-wave",        None),
        ("dc_output_current",    "DC 12V Output Current",    "A",   "current",         "measurement", "mdi:current-dc",       None),
        ("wifi_signal",          "WiFi Signal",              "dBm", "signal_strength", "measurement", "mdi:wifi",             "diagnostic"),
        ("bms_version",          "BMS Version",              None,  None,              None,          "mdi:chip",             "diagnostic"),
        ("inverter_version",     "Inverter Version",         None,  None,              None,          "mdi:sine-wave",        "diagnostic"),
        ("connection_mode",      "Connection Mode",          None,  None,              None,          "mdi:lan-connect",      "diagnostic"),
        ("lan_ip",               "LAN IP Address",           None,  None,              None,          "mdi:ip-network",       "diagnostic"),
    ]
    for key, name, unit, dev_class, state_cl, icon, category in sensors:
        payload = base(name, key, key)
        if unit:
            payload["unit_of_measurement"] = unit
        if dev_class:
            payload["device_class"] = dev_class
        if state_cl:
            payload["state_class"] = state_cl
        if icon:
            payload["icon"] = icon
        if category:
            payload["entity_category"] = category
        client.publish(f"homeassistant/sensor/oukitel_{key}/config", json.dumps(payload), retain=True)

    # Native Hardware Fault Status ENUM sensor
    payload = base("Hardware Fault Status", "hardware_fault_status", "hardware_fault_status")
    payload.update({
        "device_class": "enum",
        "options": FAULT_STATUS_OPTIONS,
        "icon": "mdi:shield-check",
        "json_attributes_topic": f"{TOPIC_BASE}/hardware_fault_status/attributes",
    })
    client.publish(f"homeassistant/sensor/oukitel_hardware_fault_status/config", json.dumps(payload), retain=True)

    # Reload session button
    payload = base("Reload Session", "reload", "reload")
    payload.update({
        "command_topic": f"{TOPIC_BASE}/reload",
        "payload_press": "PRESS",
        "icon": "mdi:reload",
        "entity_category": "diagnostic",
    })
    client.publish(f"homeassistant/button/oukitel_reload/config", json.dumps(payload), retain=True)

    # AC charging limit used to be a read-only sensor; it is now a number entity.
    client.publish("homeassistant/sensor/oukitel_ac_charging_limit/config", "", retain=True)

    # Switches (bidirectional remote controls)
    switches = [
        ("ac_switch",  "AC Output",     "mdi:power-socket-eu"),
        ("dc_switch",  "DC 12V Output", "mdi:car-electric"),
        ("usb_switch", "USB Output",    "mdi:usb-port"),
    ]
    for key, name, icon in switches:
        payload = base(name, key, key)
        payload.update({
            "command_topic": f"{TOPIC_BASE}/{key}/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "icon": icon,
            "optimistic": False,
        })
        client.publish(f"homeassistant/switch/oukitel_{key}/config", json.dumps(payload), retain=True)

    # AC charging limit slider
    payload = base("AC Charging Limit", "ac_charging_limit", "ac_charging_limit")
    payload.update({
        "command_topic": f"{TOPIC_BASE}/ac_charging_limit/set",
        "min": 3, "max": 100, "step": 1, "mode": "slider",
        "unit_of_measurement": "%",
        "icon": "mdi:gauge",
        "entity_category": "config",
    })
    client.publish("homeassistant/number/oukitel_ac_charging_limit/config", json.dumps(payload), retain=True)

    # Output frequency / voltage selectors
    selects = [
        ("output_frequency", "Output Frequency", FREQUENCY_OPTIONS, "mdi:sine-wave"),
        ("output_voltage",   "Output Voltage",   VOLTAGE_OPTIONS,   "mdi:lightning-bolt-circle"),
    ]
    for key, name, options, icon in selects:
        payload = base(name, key, key)
        payload.update({
            "command_topic": f"{TOPIC_BASE}/{key}/set",
            "options": options,
            "icon": icon,
            "entity_category": "config",
        })
        client.publish(f"homeassistant/select/oukitel_{key}/config", json.dumps(payload), retain=True)

    log.info("✅ Entities registered in Home Assistant MQTT Discovery.")


# ==========================================
# --- BRIDGE CORE ---
# ==========================================
class OukitelBridge:
    def __init__(self, cloud: AcceleronixCloudClient, device_key: str, auth_key: str):
        self.cloud = cloud
        self.device_key = device_key
        self.auth_key = auth_key
        self.mqtt = None
        self.state = {}
        self.lan = None
        self.lan_host = LAN_HOST_STATIC or None
        self.transport = None
        self._state_lock = threading.Lock()
        self._last_lan_publish = 0.0
        self._published_transport = None

    # ---- state ----
    def merge(self, values: dict) -> None:
        with self._state_lock:
            self.state.update({k: v for k, v in values.items() if v is not None})

    def lan_fresh(self) -> bool:
        return bool(
            self.lan and self.lan.connected and self.lan.last_report
            and (time.monotonic() - self.lan.last_report) < LAN_STALE_TIMEOUT
        )

    # ---- publishing ----
    def publish_state(self) -> None:
        if not self.mqtt:
            return
        with self._state_lock:
            s = dict(self.state)

        # Default ports to 0
        for k in (
            "ac_output_power", "usb_a_power", "usb_c_qc_power",
            "typec1_power", "typec2_power", "typec3_power", "typec4_power",
            "dc_output_power"
        ):
            if k not in s or s[k] is None:
                s[k] = 0

        # AC / DC voltages & currents
        ac_p = float(s.get("ac_output_power") or 0)
        ac_sw = bool(s.get("ac_switch", False))
        if s.get("ac_output_voltage") is None or s.get("ac_output_voltage") == 0:
            if ac_p > 0 or ac_sw:
                v_enum = s.get("output_voltage")
                enum_map = {0: 100, 1: 110, 2: 120, 3: 220, 4: 230}
                s["ac_output_voltage"] = enum_map.get(v_enum, 230)
            else:
                s["ac_output_voltage"] = 0

        dc_p = float(s.get("dc_output_power") or 0)
        dc_sw = bool(s.get("dc_switch", False))
        if s.get("dc_output_voltage") is None or s.get("dc_output_voltage") == 0:
            s["dc_output_voltage"] = 12.0 if (dc_p > 0 or dc_sw) else 0.0
        if s.get("dc_output_current") is None or s.get("dc_output_current") == 0:
            s["dc_output_current"] = round(dc_p / 12.0, 2) if dc_p > 0 else 0.0

        # Physics-based net power & autonomy
        total_in = float(s.get("input_watts") or 0)
        total_out = float(s.get("output_watts") or 0)
        ac_in = float(s.get("ac_input_watts") or 0)
        dc_in = float(s.get("dc_input_watts") or 0)
        ac_out = float(s.get("ac_output_power") or 0)
        dc_out = float(s.get("dc_output_power") or 0)

        real_in = max(total_in, ac_in + dc_in)
        real_out = max(total_out, ac_out + dc_out)
        net_power = real_in - real_out
        batt = float(s.get("battery") or 0)

        # Autonomy countdown
        if net_power < -5.0:
            val_dis = s.get("remain_time") or 0
            if (val_dis == 0 or val_dis >= 5940) and batt > 0:
                val_dis = int(((batt / 100.0) * 2048.0 / abs(net_power)) * 60)
            s["remain_time"] = val_dis
            s["remain_charging_time"] = 0
        elif net_power > 5.0 and batt < 100:
            val_chg = s.get("remain_charging_time") or 0
            if (val_chg == 0 or val_chg >= 5940) and batt < 100:
                needed_wh = ((100.0 - batt) / 100.0) * 2048.0
                val_chg = int((needed_wh / net_power) * 60)
            s["remain_charging_time"] = val_chg
            s["remain_time"] = 0
        else:
            s["remain_time"] = 0
            s["remain_charging_time"] = 0

        raw_lcd = s.get("remaining_time")
        if raw_lcd is None or raw_lcd == 0:
            raw_lcd = s.get("remain_time") or s.get("remain_charging_time") or 0
        s["remaining_time"] = raw_lcd

        # Hardware Fault Status ENUM calculation
        temp = float(s.get("temperature") or 0)
        fault_state = "Normal"
        details = []

        if temp >= 65.0:
            fault_state = "Over-Temperature"
            details.append(f"Temperature is critical: {temp}°C (limit 65°C)")
        elif temp < -10.0 and temp != 0:
            fault_state = "Under-Temperature"
            details.append(f"Temperature is freezing: {temp}°C")
        elif batt == 0 and real_out > 0:
            fault_state = "Critical Low Battery"
            details.append("Battery reached 0% under active load")
        elif real_out > 2400.0:
            fault_state = "Overload Protection"
            details.append(f"Output power ({real_out}W) exceeds rated 2400W")
        else:
            hw_fault = False
            for k, v in s.items():
                if isinstance(k, str) and any(x in k.lower() for x in ["fault", "alarm", "error", "protect"]):
                    if v and str(v).lower() not in ["0", "false", "none", "normal", "ok"]:
                        hw_fault = True
                        details.append(f"{k}: {v}")
            if hw_fault:
                fault_state = "Hardware Fault"
            elif temp >= 55.0:
                fault_state = "High Temperature Warning"
                details.append(f"Temperature is high: {temp}°C")
            elif batt <= 10 and real_out > 0:
                fault_state = "Low Battery Warning"
                details.append(f"Battery is low: {batt}%")

        s["hardware_fault_status"] = fault_state

        def pub(key, value):
            if value is not None:
                self.mqtt.publish(f"{TOPIC_BASE}/{key}", value, retain=False)

        for key in (
            "battery", "input_watts", "output_watts", "temperature", "ac_input_watts",
            "dc_input_watts", "remain_time", "remain_charging_time", "remaining_time",
            "ac_charging_limit", "wifi_signal", "bms_version", "inverter_version",
            "ac_output_power", "ac_output_voltage",
            "typec1_power", "typec2_power", "typec3_power", "typec4_power",
            "usb_a_power", "usb_c_qc_power",
            "dc_output_power", "dc_output_voltage", "dc_output_current",
            "hardware_fault_status",
        ):
            pub(key, s.get(key))

        for key in ("ac_switch", "dc_switch", "usb_switch"):
            if key in s:
                pub(key, "ON" if bool(s[key]) else "OFF")

        pub("output_frequency", fmt_frequency(s.get("output_frequency")))
        pub("output_voltage", fmt_voltage(s.get("output_voltage")))
        pub("connection_mode", self.transport)
        pub("lan_ip", self.lan_host or "—")

        if self.mqtt:
            attr_payload = {
                "fault_details": "; ".join(details) if details else "None",
                "possible_states": FAULT_STATUS_OPTIONS,
            }
            self.mqtt.publish(f"{TOPIC_BASE}/hardware_fault_status/attributes", json.dumps(attr_payload), retain=False)

    def set_transport(self, transport: str) -> None:
        if transport != self._published_transport:
            log.info("🔀 Active transport: %s", transport)
            self._published_transport = transport
        self.transport = transport

    def set_available(self, online: bool) -> None:
        if self.mqtt:
            self.mqtt.publish(AVAILABILITY_TOPIC, "online" if online else "offline", retain=True)

    # ---- LAN ----
    def on_lan_report(self, fields: dict) -> None:
        values = {}
        for tag, val in fields.items():
            key = LAN_TAG_MAP.get(tag)
            if key and not isinstance(val, (dict, bytes)):
                values[key] = val
        unpack_port_telemetry(fields, values)
        if not values:
            return
        self.merge(values)
        now = time.monotonic()
        if now - self._last_lan_publish >= LAN_PUBLISH_MIN_PERIOD:
            self._last_lan_publish = now
            self.set_transport("LAN")
            self.set_available(True)
            self.publish_state()

    def lan_supervisor(self) -> None:
        """Background thread: discovers the station and keeps a LAN session alive."""
        while True:
            if not (self.lan and self.lan.connected):
                try:
                    self._lan_attempt()
                except Exception as e:
                    log.warning("⚠️ [LAN] Session failed: %s", e)
            time.sleep(LAN_RETRY_DELAY if not (self.lan and self.lan.connected) else 5)

    def _lan_attempt(self) -> None:
        host = LAN_HOST_STATIC or lan_discover(self.device_key)
        if not host:
            log.info("📡 [LAN] Station not found on the local network (retry in %ss).", int(LAN_RETRY_DELAY))
            return
        self.lan_host = host
        log.info("🔌 [LAN] Connecting to %s:%s ...", host, LAN_TCP_PORT)
        link = LanLink(host, self.auth_key, self.on_lan_report)
        link.connect()
        self.lan = link
        log.info("✅ [LAN] Session established with %s — real-time push active.", host)

    # ---- commands ----
    def handle_command(self, key: str, raw: str) -> None:
        cloud_code, tag, kind = CONTROLS[key]
        raw = raw.strip()

        if kind == "bool":
            upper = raw.upper()
            if upper not in ("ON", "OFF"):
                log.warning("Unrecognized payload '%s' for %s, discarding.", raw, key)
                return
            value = upper == "ON"
            published = upper
        elif key == "output_frequency":
            if raw not in FREQUENCY_OPTIONS:
                log.warning("Invalid frequency '%s'.", raw)
                return
            value = FREQUENCY_OPTIONS.index(raw)
            published = raw
        elif key == "output_voltage":
            formatted = fmt_voltage(raw)
            if formatted not in VOLTAGE_OPTIONS:
                log.warning("Invalid voltage '%s'.", raw)
                return
            value = int(formatted[:-1])
            published = formatted
        else:
            try:
                value = max(3, min(100, int(float(raw))))
            except ValueError:
                log.warning("Invalid number '%s' for %s.", raw, key)
                return
            published = value

        log.info("📥 [Command] %s → %s", key, published)
        sent = False

        if CONNECTION_MODE != MODE_CLOUD and self.lan and self.lan.connected:
            try:
                self.lan.write(tag, kind, value)
                log.info("⚡ [LAN Command] tag %s = %s", tag, value)
                sent = True
            except Exception as e:
                log.warning("⚠️ [LAN] Write failed (%s).", e)

        if not sent and CONNECTION_MODE != MODE_LAN:
            sent = self.cloud.control_device([{cloud_code: value}])

        if sent:
            self.merge({key: value})
            self.mqtt.publish(f"{TOPIC_BASE}/{key}", published, retain=False)
        else:
            log.error("❌ Failed to apply %s = %s", key, published)

    # ---- cloud ----
    def cloud_metrics(self):
        metrics = self.cloud.get_telemetry()
        if not metrics:
            return None, False
        values = {CLOUD_KEY_MAP[k]: v for k, v in metrics.items() if k in CLOUD_KEY_MAP}
        unpack_port_telemetry(metrics, values)
        return values, bool(metrics.get("online", False))


# ==========================================
# --- MAIN LOOP ---
# ==========================================
def main():
    set_windows_console_icon()
    log.info("Starting Oukitel MQTT Bridge %s (LAN + Cloud)...", BRIDGE_VERSION)
    log.info("Configuration file: %s", CONFIG_FILE)
    log.info("🎛️ Connection mode: %s", {
        MODE_AUTO: "AUTO (LAN preferred + Cloud fallback)",
        MODE_LAN: "LAN ONLY (local, no cloud polling)",
        MODE_CLOUD: "CLOUD ONLY",
    }[CONNECTION_MODE])
    if SELECTED_REGION in REGION_SERVERS:
        reg_info = REGION_SERVERS[SELECTED_REGION]
        log.info("🌍 Selected Cloud Region: %s - %s (%s)", SELECTED_REGION, reg_info["name"], BASE_URL)
        if SELECTED_REGION != "EU":
            log.warning("⚠️ [EXPERIMENTAL NOTICE]: Region '%s' is based on official endpoints but unverified on physical units.", SELECTED_REGION)
    else:
        log.info("🌍 Selected Cloud Region: CUSTOM (%s)", BASE_URL)

    cloud = AcceleronixCloudClient(CLOUD_EMAIL, CLOUD_PASSWORD)
    cache = load_cache()

    cloud_ok = cloud.login() and cloud.fetch_device_info()
    if cloud_ok:
        if cloud.auth_key:
            save_cache({"device_key": cloud.device_key, "auth_key": cloud.auth_key})
    elif CONNECTION_MODE == MODE_LAN and cache.get("auth_key") and cache.get("device_key"):
        log.warning("⚠️ Cloud unreachable — starting LAN-only with cached authKey.")
        cloud.device_key = cache["device_key"]
        cloud.auth_key = cache["auth_key"]
    else:
        log.error("Failed to authenticate with cloud. Please verify credentials and internet access.")
        sys.exit(1)

    device_key = cloud.device_key
    auth_key = cloud.auth_key or cache.get("auth_key")
    bridge = OukitelBridge(cloud, device_key, auth_key)

    lan_enabled = CONNECTION_MODE != MODE_CLOUD and bool(auth_key)
    if CONNECTION_MODE != MODE_CLOUD and not auth_key:
        if CONNECTION_MODE == MODE_LAN:
            log.error("❌ LAN mode requested but no authKey is available.")
            sys.exit(1)
        log.warning("⚠️ No authKey available — LAN disabled, using cloud only.")

    if cloud_ok and CONNECTION_MODE != MODE_LAN:
        cloud.wake_device()

    # --- MQTT ---
    mqtt_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    mqtt_client.will_set(AVAILABILITY_TOPIC, payload="offline", qos=1, retain=True)
    bridge.mqtt = mqtt_client

    command_topics = {f"{TOPIC_BASE}/{key}/set": key for key in CONTROLS}
    reload_topic = f"{TOPIC_BASE}/reload"

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            log.info("✅ Connected to MQTT broker at %s:%s", MQTT_BROKER, MQTT_PORT)
            configure_ha_discovery(client, device_key)
            for topic in command_topics:
                client.subscribe(topic)
                log.info("📥 Subscribed to command topic: %s", topic)
            client.subscribe(reload_topic)
            log.info("📥 Subscribed to reload topic: %s", reload_topic)
        else:
            log.error("MQTT connection error: code %s", rc)

    def on_message(client, userdata, msg):
        if msg.topic == reload_topic:
            log.info("🔄 Reload command received via MQTT. Refreshing discovery & session...")
            configure_ha_discovery(client, device_key)
            if cloud_ok and CONNECTION_MODE != MODE_LAN:
                cloud.wake_device()
            return

        key = command_topics.get(msg.topic)
        if not key:
            return
        payload = msg.payload.decode(errors="replace")
        # Network I/O off the MQTT network thread
        threading.Thread(target=bridge.handle_command, args=(key, payload), daemon=True).start()

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        log.error("Could not connect to MQTT broker: %s", e)
        sys.exit(1)

    if lan_enabled:
        threading.Thread(target=bridge.lan_supervisor, name="lan-supervisor", daemon=True).start()

    log.info("Starting main loop every %s seconds.", POLL_INTERVAL)
    offline_streak = 0
    last_wake_time = time.time()
    last_snapshot = 0.0

    while True:
        try:
            if bridge.lan_fresh():
                # ---- LAN ACTIVE ----
                offline_streak = 0
                bridge.set_transport("LAN")
                bridge.set_available(True)

                # Periodic cloud snapshot for values the LAN never reports
                if (CONNECTION_MODE == MODE_AUTO and cloud.access_token
                        and time.monotonic() - last_snapshot >= CLOUD_SNAPSHOT_PERIOD):
                    last_snapshot = time.monotonic()
                    values, _online = bridge.cloud_metrics()
                    if values:
                        bridge.merge({k: values[k] for k in CLOUD_ONLY_KEYS if k in values})

                bridge.publish_state()

            elif CONNECTION_MODE == MODE_LAN:
                # ---- LAN ONLY, NO SESSION ----
                bridge.set_transport("LAN (waiting)")
                bridge.set_available(False)

            else:
                # ---- CLOUD ----
                if time.time() - last_wake_time >= WAKE_INTERVAL:
                    cloud.wake_device()
                    last_wake_time = time.time()

                values, online = bridge.cloud_metrics()
                if values and online:
                    if offline_streak > 0:
                        log.info("✅ Oukitel station back online (after %s offline cycles).", offline_streak)
                    offline_streak = 0
                    bridge.merge(values)
                    bridge.set_transport("Cloud")
                    bridge.set_available(True)
                    bridge.publish_state()

                    s = bridge.state
                    log.info(
                        "📊 [Cloud] Bat: %s%% | In: %sW (AC:%sW DC:%sW) | Out: %sW | Temp: %s°C | Remaining: %smin",
                        s.get("battery"), s.get("input_watts"), s.get("ac_input_watts"),
                        s.get("dc_input_watts"), s.get("output_watts"), s.get("temperature"),
                        s.get("remain_time"),
                    )
                else:
                    offline_streak += 1
                    if offline_streak == 1 or offline_streak % 2 == 0:
                        log.warning("⚠️ Device offline or sleeping (cycle #%s). Sending wake-up keep-alive...", offline_streak)
                        cloud.wake_device()
                        last_wake_time = time.time()

        except Exception as e:
            log.error("Exception in main loop: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
