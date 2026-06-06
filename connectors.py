"""Printer status connectors for PrintWatch."""

import json
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import requests
import websocket

HTTP_TIMEOUT = 4
_objects_cache = {}
def empty_status(extra=None):
    s = {
        "online": False,
        "state": "offline",
        "filename": None,
        "progress": 0.0,
        "time_left": None,
        "temps": {},
        "sensors": {},
        "system": {},
        "error": None,
    }
    if extra:
        s.update(extra)
    return s


def get_moonraker_objects(base, printer_id):
    """Detecte une fois les capteurs : chambre (chauffe) + autres sondes (MCU, host, coil...)."""
    if printer_id in _objects_cache:
        return _objects_cache[printer_id]
    chamber, sensors = [], []
    try:
        r = requests.get(f"{base}/printer/objects/list", timeout=HTTP_TIMEOUT)
        if r.ok:
            for name in r.json().get("result", {}).get("objects", []):
                if not (name.startswith("temperature_sensor") or name.startswith("heater_generic")):
                    continue
                low = name.lower()
                if "chamber" in low or "enclosure" in low:
                    chamber.append(name)
                else:
                    sensors.append(name)
    except (requests.RequestException, ValueError):
        pass
    _objects_cache[printer_id] = {"chamber": chamber, "sensors": sensors}
    return _objects_cache[printer_id]


def fetch_system(base):
    """Sante de l'hote : charge CPU, temperature CPU, RAM, uptime."""
    try:
        ps = requests.get(f"{base}/machine/proc_stats", timeout=HTTP_TIMEOUT).json()["result"]
    except (requests.RequestException, ValueError, KeyError):
        return {}
    mem = ps.get("system_memory") or {}
    total = mem.get("total") or 0
    return {
        "cpu": round((ps.get("system_cpu_usage") or {}).get("cpu", 0) or 0, 1),
        "cpu_temp": round(ps.get("cpu_temp") or 0, 1),
        "mem_pct": round((mem.get("used", 0) / total) * 100) if total else None,
        "uptime": int(ps.get("system_uptime") or 0),
    }


def fetch_moonraker(printer, base):
    objs = get_moonraker_objects(base, printer["id"])
    chamber_objs, sensor_objs = objs["chamber"], objs["sensors"]
    query = ["extruder", "heater_bed", "print_stats", "display_status", "virtual_sdcard"]
    query += chamber_objs + sensor_objs
    url = f"{base}/printer/objects/query?" + "&".join(q.replace(" ", "%20") for q in query)
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        st = r.json()["result"]["status"]
    except (requests.RequestException, ValueError, KeyError):
        return empty_status()

    extruder = st.get("extruder", {})
    bed = st.get("heater_bed", {})
    print_stats = st.get("print_stats", {})
    display = st.get("display_status", {})
    sdcard = st.get("virtual_sdcard", {})

    temps = {
        "extruder": {
            "actual": round(extruder.get("temperature", 0), 1),
            "target": round(extruder.get("target", 0), 1),
        },
        "bed": {
            "actual": round(bed.get("temperature", 0), 1),
            "target": round(bed.get("target", 0), 1),
        },
    }
    for obj in chamber_objs:
        data = st.get(obj, {})
        if data:
            temps["chamber"] = {
                "actual": round(data.get("temperature", 0), 1),
                "target": round(data.get("target", 0), 1),
            }
            break

    # Capteurs additionnels (MCU, carte, coil...) -> nom lisible : valeur
    sensors = {}
    for obj in sensor_objs:
        data = st.get(obj, {})
        if data and data.get("temperature") is not None:
            pretty = obj.split(" ", 1)[-1].replace("_", " ")
            sensors[pretty] = round(data.get("temperature", 0), 1)

    progress = display.get("progress") or sdcard.get("progress") or 0.0
    print_duration = print_stats.get("print_duration", 0) or 0
    time_left = None
    if progress > 0.01 and print_duration > 0:
        est_total = print_duration / progress
        time_left = max(0, int(est_total - print_duration))

    return {
        "online": True,
        "state": print_stats.get("state", "unknown"),
        "filename": print_stats.get("filename") or None,
        "progress": round(progress, 4),
        "time_left": time_left,
        "temps": temps,
        "sensors": sensors,
        "system": fetch_system(base),
        "error": None,
    }


def fetch_octoprint(printer, base):
    headers = {"X-Api-Key": printer.get("apikey", "")}
    try:
        pr = requests.get(f"{base}/api/printer", headers=headers, timeout=HTTP_TIMEOUT)
        jb = requests.get(f"{base}/api/job", headers=headers, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return empty_status()

    if pr.status_code == 403 or jb.status_code == 403:
        return empty_status({"error": "Cle API OctoPrint manquante ou invalide"})
    if not pr.ok or not jb.ok:
        return empty_status()

    try:
        pdata = pr.json()
        jdata = jb.json()
    except ValueError:
        return empty_status()

    temps = {}
    for key, label in (("tool0", "extruder"), ("bed", "bed"), ("chamber", "chamber")):
        t = pdata.get("temperature", {}).get(key)
        if t:
            temps[label] = {
                "actual": round(t.get("actual") or 0, 1),
                "target": round(t.get("target") or 0, 1),
            }

    progress = (jdata.get("progress", {}).get("completion") or 0) / 100.0
    return {
        "online": True,
        "state": pdata.get("state", {}).get("text", "unknown").lower(),
        "filename": (jdata.get("job", {}).get("file", {}) or {}).get("name"),
        "progress": round(progress, 4),
        "time_left": jdata.get("progress", {}).get("printTimeLeft"),
        "temps": temps,
        "error": None,
    }


def _ff_get(data, *names, default=None):
    """Recherche tolerante dans les reponses FlashForge."""
    if not isinstance(data, dict):
        return default
    wanted = {n.lower() for n in names}
    stack = [data]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, dict):
            continue
        for k, v in cur.items():
            if str(k).lower() in wanted:
                return v
            if isinstance(v, dict):
                stack.append(v)
    return default


def _safe_float(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_flashforge_5m(printer, base):
    """FlashForge Adventurer 5M/5M Pro : statut via HTTP POST /detail (port 8898)."""
    serial = (printer.get("serial") or "").strip()
    check_code = (printer.get("apikey") or "").strip()
    if not serial or not check_code:
        return empty_status({
            "error": "FlashForge : serialNumber et checkCode requis."
        })

    try:
        r = requests.post(
            f"{base}/detail",
            json={"serialNumber": serial, "checkCode": check_code},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        raw = r.json()
    except (requests.RequestException, ValueError):
        return empty_status({"error": "FlashForge : connexion ou authentification impossible"})

    state = str(_ff_get(raw, "status", "machineStatus", "printerStatus", default="unknown")).lower()
    progress = _safe_float(_ff_get(raw, "printProgress", "progress", "printPercent", default=0))
    if progress > 1:
        progress = progress / 100

    time_left = _ff_get(raw, "remainingTime", "leftTime", "printRemainingTime", default=None)
    try:
        time_left = int(time_left) if time_left is not None else None
    except (TypeError, ValueError):
        time_left = None

    temps = {
        "extruder": {
            "actual": round(_safe_float(_ff_get(raw, "leftTemp", "rightTemp", "nozzleTemp", "extruderTemp")), 1),
            "target": round(_safe_float(_ff_get(raw, "leftTargetTemp", "rightTargetTemp",
                                                "nozzleTargetTemp", "extruderTargetTemp")), 1),
        },
        "bed": {
            "actual": round(_safe_float(_ff_get(raw, "platTemp", "bedTemp", "platformTemp")), 1),
            "target": round(_safe_float(_ff_get(raw, "platTargetTemp", "bedTargetTemp",
                                                "platformTargetTemp")), 1),
        },
    }

    return {
        "online": True,
        "state": state,
        "filename": _ff_get(raw, "printFileName", "fileName", "filename", default=None),
        "progress": round(max(0, min(progress, 1)), 4),
        "time_left": time_left,
        "temps": temps,
        "sensors": {},
        "system": {},
        "error": None,
    }


SDCP_MACHINE_STATES = {
    0: "idle",
    1: "printing",
    2: "paused",
    3: "error",
}

SDCP_PRINT_STATES = {
    0: "idle",
    1: "printing",   # homing / preparing
    2: "printing",
    3: "printing",
    4: "printing",
    5: "pausing",
    6: "paused",
    7: "cancelled",
    8: "cancelled",
    9: "complete",
    10: "printing",
}


def _deep_find_dict(data, *names):
    wanted = {n.lower() for n in names}
    stack = [data]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, dict):
            continue
        for k, v in cur.items():
            if str(k).lower() in wanted and isinstance(v, dict):
                return v
            if isinstance(v, dict):
                stack.append(v)
            elif isinstance(v, list):
                stack.extend(x for x in v if isinstance(x, dict))
    return {}


def _sdcp_request(cmd, mainboard_id=""):
    request_id = uuid.uuid4().hex
    return {
        "Id": uuid.uuid4().hex,
        "Topic": f"sdcp/request/{mainboard_id}",
        "Data": {
            "Cmd": cmd,
            "Data": {},
            "RequestID": request_id,
            "MainboardID": mainboard_id,
            "TimeStamp": int(time.time()),
            "From": 0,
        },
    }


def _sdcp_status_from_message(message):
    if not isinstance(message, dict):
        return {}
    status = _deep_find_dict(message, "Status", "last_status")
    if status:
        return status
    # Certains clients renvoient directement le bloc status.
    if "TempOfNozzle" in message or "PrintInfo" in message:
        return message
    return {}


def fetch_elegoo_sdcp_fdm(printer, base):
    """Elegoo Centauri Carbon / SDCP v3 FDM : statut via WebSocket local."""
    mainboard_id = (printer.get("serial") or "").strip()
    try:
        ws = websocket.create_connection(base, timeout=HTTP_TIMEOUT)
        ws.settimeout(HTTP_TIMEOUT)
        # Le statut est souvent pousse automatiquement ; on demande quand meme un refresh.
        ws.send("ping")
        try:
            ws.recv()
        except Exception:
            pass
        ws.send(json.dumps(_sdcp_request(0, mainboard_id)))

        status = {}
        for _ in range(6):
            raw = ws.recv()
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            status = _sdcp_status_from_message(msg)
            if status:
                break
        ws.close()
    except Exception:
        return empty_status({"error": "Elegoo SDCP : connexion WebSocket impossible"})

    if not status:
        return empty_status({"error": "Elegoo SDCP: no status received"})

    pinfo = status.get("PrintInfo") or {}
    current = status.get("CurrentStatus")
    if isinstance(current, list):
        current = current[0] if current else 0
    print_state = pinfo.get("Status")
    state = SDCP_PRINT_STATES.get(print_state, SDCP_MACHINE_STATES.get(current, "unknown"))

    current_ticks = _safe_float(pinfo.get("CurrentTicks"), 0)
    total_ticks = _safe_float(pinfo.get("TotalTicks"), 0)
    progress = current_ticks / total_ticks if total_ticks > 0 else 0
    current_layer = _safe_float(pinfo.get("CurrentLayer"), 0)
    total_layer = _safe_float(pinfo.get("TotalLayer"), 0)
    if not progress and total_layer > 0:
        progress = current_layer / total_layer

    time_left = int(max(0, total_ticks - current_ticks)) if total_ticks > 0 else None

    temps = {
        "extruder": {
            "actual": round(_safe_float(status.get("TempOfNozzle")), 1),
            "target": round(_safe_float(status.get("TempTargetNozzle")), 1),
        },
        "bed": {
            "actual": round(_safe_float(status.get("TempOfHotbed")), 1),
            "target": round(_safe_float(status.get("TempTargetHotbed")), 1),
        },
    }
    if status.get("TempOfBox") is not None:
        temps["chamber"] = {
            "actual": round(_safe_float(status.get("TempOfBox")), 1),
            "target": round(_safe_float(status.get("TempTargetBox")), 1),
        }

    return {
        "online": True,
        "state": state,
        "filename": pinfo.get("Filename") or None,
        "progress": round(max(0, min(progress, 1)), 4),
        "time_left": time_left,
        "temps": temps,
        "sensors": {},
        "system": {},
        "error": None,
    }


BAMBU_STATES = {
    "IDLE": "idle",
    "PREPARE": "printing",
    "RUNNING": "printing",
    "PAUSE": "paused",
    "PAUSED": "paused",
    "FINISH": "complete",
    "FAILED": "error",
    "FAILED_STOP": "error",
    "SLICING": "printing",
}


def fetch_bambulab_mqtt(printer, base):
    """Bambu Lab : statut local via MQTT TLS 8883 (LAN mode / developer mode)."""
    host = (printer.get("host") or "").strip()
    serial = (printer.get("serial") or "").strip()
    access_code = (printer.get("apikey") or "").strip()
    if not serial or not access_code:
        return empty_status({
            "error": "Bambu Lab : serialNumber et LAN Access Code requis."
        })

    report_topic = f"device/{serial}/report"
    request_topic = f"device/{serial}/request"
    received = {}
    done = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties=None):
        try:
            client.subscribe(report_topic)
            client.publish(request_topic, json.dumps({"pushing": {
                "sequence_id": str(int(time.time())),
                "command": "pushall",
            }}))
        except Exception:
            pass

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8", "ignore"))
        except (ValueError, UnicodeDecodeError):
            return
        if "print" in payload:
            received.update(payload["print"] or {})
            done.set()

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"printwatch-{uuid.uuid4().hex[:8]}")
        client.username_pw_set("bblp", access_code)
        client.tls_set()
        client.tls_insecure_set(True)
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(host, 8883, keepalive=10)
        client.loop_start()
        done.wait(HTTP_TIMEOUT)
        client.loop_stop()
        client.disconnect()
    except Exception:
        return empty_status({"error": "Bambu Lab : connexion MQTT impossible"})

    if not received:
        return empty_status({"error": "Bambu Lab: no MQTT status received"})

    raw_state = str(received.get("gcode_state") or received.get("stg_cur") or "unknown")
    state = BAMBU_STATES.get(raw_state.upper(), raw_state.lower())
    progress = _safe_float(received.get("mc_percent"), 0)
    if progress > 1:
        progress /= 100

    time_left = received.get("mc_remaining_time")
    try:
        # Bambu expose souvent les minutes restantes.
        time_left = int(float(time_left) * 60) if time_left is not None else None
    except (TypeError, ValueError):
        time_left = None

    filename = (received.get("subtask_name") or received.get("gcode_file") or
                received.get("project_name") or None)

    temps = {
        "extruder": {
            "actual": round(_safe_float(received.get("nozzle_temper")), 1),
            "target": round(_safe_float(received.get("nozzle_target_temper")), 1),
        },
        "bed": {
            "actual": round(_safe_float(received.get("bed_temper")), 1),
            "target": round(_safe_float(received.get("bed_target_temper")), 1),
        },
    }
    if received.get("chamber_temper") is not None:
        temps["chamber"] = {
            "actual": round(_safe_float(received.get("chamber_temper")), 1),
            "target": 0,
        }

    sensors = {}
    if received.get("layer_num") is not None and received.get("total_layer_num") is not None:
        sensors["Layer"] = f"{received.get('layer_num')}/{received.get('total_layer_num')}"
    if received.get("wifi_signal") is not None:
        sensors["Wi-Fi"] = received.get("wifi_signal")

    return {
        "online": True,
        "state": state,
        "filename": filename,
        "progress": round(max(0, min(progress, 1)), 4),
        "time_left": time_left,
        "temps": temps,
        "sensors": sensors,
        "system": {},
        "error": None,
    }


def fetch_status(printer):
    ptype = printer.get("type")
    base = printer.get("base_url")
    if ptype == "moonraker":
        return fetch_moonraker(printer, base)
    if ptype == "octoprint":
        return fetch_octoprint(printer, base)
    if ptype == "flashforge_5m":
        return fetch_flashforge_5m(printer, base)
    if ptype == "elegoo_sdcp_fdm":
        return fetch_elegoo_sdcp_fdm(printer, base)
    if ptype == "bambulab_mqtt":
        return fetch_bambulab_mqtt(printer, base)
    if ptype == "camera_only":
        return {**empty_status(), "online": True, "state": "camera", "error": None}
    return empty_status({"error": "Protocole inconnu"})



