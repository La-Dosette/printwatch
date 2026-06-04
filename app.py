"""
PrintWatch - Agent local de monitoring d'imprimantes 3D.

Agent SANS ETAT : il detecte le protocole via des connecteurs (Moonraker,
OctoPrint, FlashForge 5M...) et relaie vers les imprimantes. Toute la
configuration vit cote navigateur (localStorage) ;
l'hote/le type sont passes en parametres d'URL. L'agent sert aussi l'UI statique
(dossier docs/), la meme qui peut etre hebergee sur GitHub Pages.

Lancement :
    pip install -r requirements.txt
    python app.py
Puis ouvre http://localhost:8088 dans ton navigateur.
"""

import os
import re
import threading
import time

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "docs")  # racine web (sert aussi pour GitHub Pages)

# L'agent sert la meme UI statique que GitHub Pages (dossier docs/).
app = Flask(__name__, static_folder=os.path.join(DOCS_DIR, "static"), static_url_path="/static")


@app.after_request
def add_cors(resp):
    """CORS ouvert : permet a l'UI hebergee (github.io) d'appeler cet agent local."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Api-Key"
    # Private Network Access : autorise une origine publique (https) a joindre le LAN/localhost
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def cors_preflight(_any):
    return ("", 204)

# Presets de prechauffe (temperature buse / plateau)
PREHEAT = {
    "PLA": {"ext": 210, "bed": 60},
    "PETG": {"ext": 240, "bed": 85},
    "ABS": {"ext": 250, "bed": 100},
}

# Timeout court : une imprimante eteinte ne doit pas bloquer le dashboard.
HTTP_TIMEOUT = 4

# Cache des objets capteurs detectes par imprimante (evite un appel a chaque poll).
_objects_cache = {}


# --------------------------------------------------------------------------
# Alertes Discord (webhook) + surveillance en arriere-plan
# --------------------------------------------------------------------------
def discord_send(webhook, title, desc, color):
    if not webhook:
        return False
    try:
        requests.post(webhook, json={"embeds": [{
            "title": title, "description": desc, "color": color,
            "footer": {"text": "PrintWatch"},
        }]}, timeout=HTTP_TIMEOUT)
        return True
    except requests.RequestException:
        return False


_last_states = {}  # id -> dernier etat connu (pour detecter les transitions)


def _handle_transition(printer, prev, cur, status, webhook, alerts):
    name = printer["name"]
    fname = status.get("filename") or "—"
    if cur == "complete" and prev in ("printing", "paused") and alerts.get("complete", True):
        discord_send(webhook, f"✅ {name} — Impression terminée", f"Fichier : **{fname}**", 0x34E0A1)
    elif "error" in str(cur) and alerts.get("error", True):
        discord_send(webhook, f"❌ {name} — Erreur", f"État : {cur}\nFichier : {fname}", 0xFF6B6B)
    elif cur == "offline" and prev != "offline" and alerts.get("offline", False):
        discord_send(webhook, f"⚠️ {name} — Déconnectée", "L'imprimante ne répond plus.", 0xFF9F43)
    elif prev == "offline" and cur != "offline" and alerts.get("offline", False):
        discord_send(webhook, f"🔌 {name} — Reconnectée", "L'imprimante répond à nouveau.", 0x6C8CFF)


def _printer_from_cfg(item):
    """Construit une imprimante depuis un item pousse par le navigateur."""
    host = (item.get("host") or "").strip()
    ptype = item.get("type") or ""
    if ptype == "moonraker":
        base = f"http://{host}:7125"
    elif ptype == "flashforge_5m":
        base = f"http://{host}:8898"
    else:
        base = f"http://{host}"
    return {"id": item.get("id") or host, "name": item.get("name") or host, "host": host,
            "type": ptype, "base_url": base, "apikey": item.get("apikey") or "",
            "serial": item.get("serial") or "",
            "webcam": item.get("webcam") or ""}


def monitor_loop():
    """Boucle de fond : detecte les changements d'etat et envoie les alertes Discord.

    La config (webhook, alertes, liste d'imprimantes) est poussee par le navigateur
    via POST /api/monitor et stockee dans _monitor_cfg (agent sans persistance).
    """
    while True:
        try:
            webhook = _monitor_cfg.get("webhook")
            alerts = _monitor_cfg.get("alerts", {})
            for item in _monitor_cfg.get("printers", []):
                p = _printer_from_cfg(item)
                if not p["host"]:
                    continue
                status = fetch_status(p)
                cur = status.get("state") if status.get("online") else "offline"
                prev = _last_states.get(p["id"])
                if webhook and prev is not None and cur != prev:
                    _handle_transition(p, prev, cur, status, webhook, alerts)
                _last_states[p["id"]] = cur
        except Exception:
            pass
        time.sleep(15)


# --------------------------------------------------------------------------
# Detection du protocole a partir d'une simple IP
# --------------------------------------------------------------------------
def detect_protocol(host, apikey=None):
    """Sonde l'hote pour deviner le firmware. Retourne (type, base_url) ou (None, None)."""
    host = host.strip().rstrip("/")
    # L'utilisateur peut coller une URL complete ; on garde juste l'hote.
    if host.startswith("http://"):
        host = host[len("http://"):]
    elif host.startswith("https://"):
        host = host[len("https://"):]
    host = host.split("/")[0]
    host_only = host.split(":")[0]

    # 1) Moonraker (Klipper) - port 7125, pas d'auth par defaut. Cas le plus courant.
    try:
        base = f"http://{host_only}:7125"
        r = requests.get(f"{base}/printer/info", timeout=HTTP_TIMEOUT)
        if r.ok and "result" in r.json():
            return "moonraker", base
    except (requests.RequestException, ValueError):
        pass

    # 2) OctoPrint - port 80 (ou celui fourni), necessite une cle API pour les donnees.
    try:
        base = f"http://{host}" if ":" in host else f"http://{host}"
        headers = {"X-Api-Key": apikey} if apikey else {}
        r = requests.get(f"{base}/api/version", headers=headers, timeout=HTTP_TIMEOUT)
        # 200 = ok ; 403 = OctoPrint present mais cle manquante/invalide.
        if r.status_code in (200, 403):
            return "octoprint", base
    except requests.RequestException:
        pass

    # 3) FlashForge Adventurer 5M / 5M Pro - API HTTP locale sur 8898.
    # Le statut complet exige serialNumber + checkCode, mais la presence du port
    # suffit pour proposer le connecteur et demander ces champs a l'utilisateur.
    try:
        base = f"http://{host_only}:8898"
        r = requests.post(f"{base}/detail", json={}, timeout=HTTP_TIMEOUT)
        if r.status_code in (200, 400, 401, 403, 405):
            return "flashforge_5m", base
    except requests.RequestException:
        pass

    return None, None


# --------------------------------------------------------------------------
# Recuperation de l'etat, normalise vers un format commun
# --------------------------------------------------------------------------
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


def fetch_status(printer):
    ptype = printer.get("type")
    base = printer.get("base_url")
    if ptype == "moonraker":
        return fetch_moonraker(printer, base)
    if ptype == "octoprint":
        return fetch_octoprint(printer, base)
    if ptype == "flashforge_5m":
        return fetch_flashforge_5m(printer, base)
    return empty_status({"error": "Protocole inconnu"})


def _clean_ftype(val):
    """Normalise le type de filament. Le multi-materiaux (MMU) arrive comme '["ABS","ABS"]'."""
    if not val:
        return "Inconnu"
    s = str(val)
    if "[" in s or "," in s:
        seen = []
        for part in re.findall(r"[A-Za-z0-9\-]+", s):
            if part not in seen:
                seen.append(part)
        return "+".join(seen) if seen else "Inconnu"
    return s


def _raw_history(base, limit=1000):
    """Recupere la liste brute des jobs Moonraker (du plus recent au plus ancien)."""
    try:
        r = requests.get(f"{base}/server/history/list?limit={limit}&order=desc", timeout=HTTP_TIMEOUT)
        return r.json()["result"]["jobs"]
    except (requests.RequestException, ValueError, KeyError):
        return []


def _thumb_path(job, md):
    """Reconstruit le chemin (relatif a la racine gcode) de la plus grande miniature."""
    thumbs = md.get("thumbnails") or []
    if not thumbs:
        return None
    best = max(thumbs, key=lambda x: x.get("size", 0))
    rel = best.get("relative_path", "")
    folder = os.path.dirname(job.get("filename", ""))
    return (folder + "/" + rel) if folder else rel


def _normalize_job(j):
    md = j.get("metadata") or {}
    return {
        "filename": j.get("filename"),
        "status": j.get("status"),
        "end_time": j.get("end_time") or j.get("start_time"),
        "duration": j.get("total_duration") or j.get("print_duration") or 0,
        "filament": j.get("filament_used") or 0,
        "filament_type": _clean_ftype(md.get("filament_type")) if md.get("filament_type") else None,
        "weight": md.get("filament_weight_total"),
        "thumb": _thumb_path(j, md),
        "meta": {
            "slicer": md.get("slicer"),
            "slicer_version": md.get("slicer_version"),
            "layers": md.get("layer_count"),
            "height": md.get("object_height"),
            "layer_height": md.get("layer_height"),
            "nozzle": md.get("nozzle_diameter"),
            "est_time": md.get("estimated_time"),
            "length": md.get("filament_total"),
            "colors": md.get("filament_colors") or [],
        },
    }


def fetch_history(printer):
    """Historique Moonraker : totaux, taux de reussite, activite 30j, filament/type, jobs recents."""
    base = printer.get("base_url")
    if printer.get("type") != "moonraker":
        return {"supported": False, "totals": {}, "jobs": [],
                "success_rate": None, "activity": [], "by_type": {}}

    out = {"supported": True, "totals": {}, "jobs": [],
           "success_rate": None, "activity": [], "by_type": {}}

    try:
        jt = requests.get(f"{base}/server/history/totals", timeout=HTTP_TIMEOUT).json()["result"]["job_totals"]
        out["totals"] = {
            "total_jobs": int(jt.get("total_jobs", 0)),
            "total_time": jt.get("total_time", 0) or 0,
            "total_filament": jt.get("total_filament_used", 0) or 0,
            "longest_print": jt.get("longest_print", 0) or 0,
        }
    except (requests.RequestException, ValueError, KeyError):
        pass

    raw = _raw_history(base)

    # Activite des 30 derniers jours (buckets par date locale)
    day_secs = 86400
    today = int(time.time() // day_secs) * day_secs
    buckets = {today - i * day_secs: {"count": 0, "completed": 0} for i in range(29, -1, -1)}

    completed = finished = 0
    by_type = {}
    for j in raw:
        status = j.get("status")
        if status != "in_progress":
            finished += 1
            if status == "completed":
                completed += 1
        # Filament par type (en grammes)
        md = j.get("metadata") or {}
        ftype = _clean_ftype(md.get("filament_type"))
        weight = md.get("filament_weight_total") or 0
        if weight:
            by_type[ftype] = round(by_type.get(ftype, 0) + weight, 1)
        # Bucket d'activite
        et = j.get("end_time") or j.get("start_time")
        if et:
            d = int(et // day_secs) * day_secs
            if d in buckets:
                buckets[d]["count"] += 1
                if status == "completed":
                    buckets[d]["completed"] += 1

    out["jobs"] = [_normalize_job(j) for j in raw[:25]]
    out["by_type"] = by_type
    out["activity"] = [{"date": d, "count": v["count"], "completed": v["completed"]}
                       for d, v in sorted(buckets.items())]
    if finished:
        out["success_rate"] = round(completed / finished * 100)
    return out


# --------------------------------------------------------------------------
# Routes (UI statique + API agent)
# --------------------------------------------------------------------------
# L'agent sert exactement la meme UI statique que GitHub Pages (dossier docs/).
@app.route("/")
def index():
    return send_from_directory(DOCS_DIR, "index.html")


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(DOCS_DIR, "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/favicon.svg")
@app.route("/icon.svg")
def icon():
    return send_from_directory(DOCS_DIR, "icon.svg", mimetype="image/svg+xml")


@app.route("/sw.js")
def service_worker():
    return send_from_directory(DOCS_DIR, "sw.js", mimetype="text/javascript")


def _moonraker_post(base, path):
    requests.post(f"{base}{path}", timeout=HTTP_TIMEOUT).raise_for_status()


def _moonraker_gcode(base, script):
    requests.post(f"{base}/printer/gcode/script", params={"script": script},
                  timeout=HTTP_TIMEOUT).raise_for_status()


# ==========================================================================
# AGENT SANS ETAT — endpoints parametres par l'hote (config cote navigateur)
# L'UI (hebergeable sur github.io) stocke la config en localStorage et passe
# host/type/apikey en parametres. L'agent ne stocke rien.
# ==========================================================================
def printer_from_params():
    host = (request.args.get("host") or "").strip()
    ptype = (request.args.get("type") or "").strip()
    apikey = (request.args.get("apikey") or "").strip()
    serial = (request.args.get("serial") or "").strip()
    if ptype == "moonraker":
        base = f"http://{host}:7125"
    elif ptype == "flashforge_5m":
        base = f"http://{host}:8898"
    else:
        base = f"http://{host}"
    return {"id": host, "name": request.args.get("name") or host, "host": host,
            "type": ptype, "base_url": base, "apikey": apikey, "serial": serial,
            "webcam": request.args.get("webcam", "")}


@app.route("/api/detect")
def api_detect():
    host = (request.args.get("host") or "").strip()
    apikey = (request.args.get("apikey") or "").strip()
    if not host:
        return jsonify({"error": "Adresse requise"}), 400
    ptype, base = detect_protocol(host, apikey)
    if not ptype:
        return jsonify({"error": "Aucune imprimante detectee a cette adresse. "
                        "Verifie l'IP / que l'agent et la machine sont sur le reseau."}), 422
    clean = host
    for pre in ("http://", "https://"):
        if clean.startswith(pre):
            clean = clean[len(pre):]
    clean = clean.split("/")[0].split(":")[0] if ptype == "moonraker" else clean.split("/")[0]
    return jsonify({"type": ptype, "host": clean, "base": base})


@app.route("/api/status")
def api_status_q():
    p = printer_from_params()
    if not p["host"]:
        return jsonify({"error": "host requis"}), 400
    return jsonify(fetch_status(p))


@app.route("/api/stats")
def api_stats_q():
    return jsonify(fetch_history(printer_from_params()))


@app.route("/api/stats.csv")
def api_stats_csv_q():
    p = printer_from_params()
    if p.get("type") != "moonraker":
        return Response(status=404)
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["date", "fichier", "statut", "duree_min", "filament_mm", "poids_g", "type"])
    for j in _raw_history(p["base_url"]):
        nj = _normalize_job(j)
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(nj["end_time"])) if nj["end_time"] else ""
        w.writerow([date, nj["filename"], nj["status"], round((nj["duration"] or 0) / 60, 1),
                    round(nj["filament"] or 0), nj["weight"] or "", nj["filament_type"] or ""])
    fname = f"printwatch_{p['name']}.csv".replace(" ", "_")
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@app.route("/api/control", methods=["POST"])
def api_control_q():
    p = printer_from_params()
    if p.get("type") != "moonraker":
        return jsonify({"error": "Controles disponibles uniquement pour Moonraker"}), 422
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action")
    base = p["base_url"]
    try:
        if action == "pause":
            _moonraker_post(base, "/printer/print/pause")
        elif action == "resume":
            _moonraker_post(base, "/printer/print/resume")
        elif action == "cancel":
            _moonraker_post(base, "/printer/print/cancel")
        elif action == "estop":
            _moonraker_post(base, "/printer/emergency_stop")
        elif action == "cooldown":
            _moonraker_gcode(base, "TURN_OFF_HEATERS")
        elif action == "preheat":
            preset = PREHEAT.get(data.get("material"))
            if not preset:
                return jsonify({"error": "Materiau inconnu"}), 400
            _moonraker_gcode(base, f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={preset['ext']}")
            _moonraker_gcode(base, f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={preset['bed']}")
        else:
            return jsonify({"error": "Action inconnue"}), 400
    except requests.RequestException as e:
        return jsonify({"error": f"Echec de la commande : {e}"}), 502
    return jsonify({"ok": True})


@app.route("/api/webcam")
def api_webcam_q():
    url = request.args.get("url", "")
    if not url:
        return Response(status=404)
    try:
        upstream = requests.get(url, stream=True, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return Response(status=502)
    ctype = upstream.headers.get("Content-Type", "multipart/x-mixed-replace")

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=4096):
                yield chunk
        except requests.RequestException:
            return
    return Response(stream_with_context(generate()), content_type=ctype)


@app.route("/api/thumb")
def api_thumb_q():
    p = printer_from_params()
    path = request.args.get("path", "")
    if p.get("type") != "moonraker" or not path:
        return Response(status=404)
    try:
        r = requests.get(f"{p['base_url']}/server/files/gcodes/{path}", timeout=HTTP_TIMEOUT)
        if not r.ok:
            return Response(status=404)
        return Response(r.content, content_type=r.headers.get("Content-Type", "image/png"))
    except requests.RequestException:
        return Response(status=502)


# --- Surveillance / alertes Discord : config poussee par le navigateur ---
_monitor_cfg = {"webhook": "", "alerts": {"complete": True, "error": True, "offline": False}, "printers": []}


@app.route("/api/monitor", methods=["POST"])
def api_monitor():
    data = request.get_json(force=True, silent=True) or {}
    _monitor_cfg["webhook"] = (data.get("webhook") or "").strip()
    _monitor_cfg["alerts"] = {**_monitor_cfg["alerts"], **(data.get("alerts") or {})}
    _monitor_cfg["printers"] = data.get("printers") or []
    return jsonify({"ok": True})


@app.route("/api/monitor/test", methods=["POST"])
def api_monitor_test():
    data = request.get_json(force=True, silent=True) or {}
    wh = (data.get("webhook") or "").strip() or _monitor_cfg["webhook"]
    if not wh:
        return jsonify({"error": "Aucun webhook configure"}), 400
    ok = discord_send(wh, "🔔 PrintWatch — Test", "Les alertes Discord fonctionnent !", 0x6C8CFF)
    return (jsonify({"ok": True}) if ok else (jsonify({"error": "Envoi echoue"}), 502))


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "agent": "printwatch", "version": 1})


if __name__ == "__main__":
    threading.Thread(target=monitor_loop, daemon=True).start()
    print("Agent PrintWatch demarre sur http://localhost:8088")
    app.run(host="0.0.0.0", port=8088, threaded=True)
