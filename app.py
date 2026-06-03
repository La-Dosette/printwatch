"""
PrintWatch - Dashboard universel de monitoring d'imprimantes 3D.

Tu mets l'IP, le backend detecte le protocole (Moonraker / OctoPrint) et
expose un etat normalise (statut, progression, temperatures, webcam) au
dashboard web.

Lancement :
    pip install -r requirements.txt
    python app.py
Puis ouvre http://localhost:8088 dans ton navigateur.
"""

import json
import os
import re
import threading
import time
import uuid

import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # recharge index.html sans redemarrer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(BASE_DIR, "printers.json")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
LAYOUTS_PATH  = os.path.join(BASE_DIR, "layouts.json")

DEFAULT_SETTINGS = {
    "discord_webhook": "",
    "alerts": {"complete": True, "error": True, "offline": False},
    "appearance": {"name": "PrintWatch", "accent": "#6c8cff", "accent2": "#a06bff", "logo": "",
                   "anim": "full", "bg": "color", "font": "rounded", "radius": "normal",
                   "density": "comfort", "skin": "archive"},
}

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
# Persistance (un simple fichier JSON, pas de base de donnees a installer)
# --------------------------------------------------------------------------
def load_printers():
    if not os.path.exists(STORE_PATH):
        return []
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_printers(printers):
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(printers, f, indent=2, ensure_ascii=False)


def find_printer(printer_id):
    return next((p for p in load_printers() if p["id"] == printer_id), None)


def load_layouts():
    if not os.path.exists(LAYOUTS_PATH):
        return {"active": "", "layouts": {}}
    try:
        with open(LAYOUTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"active": "", "layouts": {}}


def save_layouts(data):
    with open(LAYOUTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_settings():
    if not os.path.exists(SETTINGS_PATH):
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        merged["alerts"] = {**DEFAULT_SETTINGS["alerts"], **(data.get("alerts") or {})}
        merged["appearance"] = {**DEFAULT_SETTINGS["appearance"], **(data.get("appearance") or {})}
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


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


def monitor_loop():
    """Boucle de fond : detecte les changements d'etat et envoie les alertes Discord."""
    while True:
        try:
            settings = load_settings()
            webhook = settings.get("discord_webhook")
            alerts = settings.get("alerts", {})
            for p in load_printers():
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


def fetch_status(printer):
    ptype = printer.get("type")
    base = printer.get("base_url")
    if ptype == "moonraker":
        return fetch_moonraker(printer, base)
    if ptype == "octoprint":
        return fetch_octoprint(printer, base)
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


def default_webcam_url(printer):
    """Si l'utilisateur n'a pas fourni d'URL webcam, on tente le chemin standard."""
    if printer.get("webcam"):
        return printer["webcam"]
    host = printer.get("host", "")
    return f"http://{host}/webcam/?action=stream"


# --------------------------------------------------------------------------
# Routes API
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# --- PWA : installable comme une appli, fonctionne en plein ecran / hors-ligne (coquille) ---
MANIFEST = {
    "name": "PrintWatch", "short_name": "PrintWatch",
    "description": "Monitoring universel d'imprimantes 3D",
    "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#070810", "theme_color": "#070810",
    "icons": [
        {"src": "/static/logo.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        {"src": "/icon.svg",        "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"},
    ],
}

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0%" stop-color="#6c8cff"/><stop offset="100%" stop-color="#a06bff"/></linearGradient></defs>
<rect width="512" height="512" rx="112" fill="#0b0e18"/>
<rect x="56" y="56" width="400" height="400" rx="96" fill="url(#g)"/>
<g fill="none" stroke="#fff" stroke-width="26" stroke-linecap="round" stroke-linejoin="round">
<path d="M160 360 L160 208 L256 150 L352 208 L352 360"/><path d="M160 288 L352 288"/>
<path d="M208 360 L208 412 L304 412 L304 360"/></g></svg>"""

SERVICE_WORKER = """
const CACHE = "printwatch-v1";
self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api")) return;               // donnees live : toujours le reseau
  e.respondWith(
    fetch(e.request).then(r => {
      const copy = r.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
      return r;
    }).catch(() => caches.match(e.request))                  // hors-ligne : coquille en cache
  );
});
"""


@app.route("/manifest.webmanifest")
def manifest():
    return Response(json.dumps(MANIFEST), mimetype="application/manifest+json")


@app.route("/favicon.svg")
@app.route("/icon.svg")
def icon():
    # Sert le vrai logo si disponible, sinon le SVG généré
    logo_path = os.path.join(BASE_DIR, "static", "logo.svg")
    if os.path.exists(logo_path):
        with open(logo_path, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="image/svg+xml")
    return Response(ICON_SVG, mimetype="image/svg+xml")


@app.route("/sw.js")
def service_worker():
    return Response(SERVICE_WORKER, mimetype="text/javascript",
                    headers={"Service-Worker-Allowed": "/"})


@app.route("/api/printers", methods=["GET"])
def api_list():
    out = []
    for p in load_printers():
        out.append({
            "id": p["id"],
            "name": p["name"],
            "host": p["host"],
            "type": p.get("type"),
            "webcam": default_webcam_url(p),
        })
    return jsonify(out)


@app.route("/api/printers", methods=["POST"])
def api_add():
    data = request.get_json(force=True, silent=True) or {}
    host = (data.get("host") or "").strip()
    if not host:
        return jsonify({"error": "Adresse IP / hote requis"}), 400

    name = (data.get("name") or "").strip() or host
    apikey = (data.get("apikey") or "").strip()
    webcam = (data.get("webcam") or "").strip()

    ptype, base = detect_protocol(host, apikey)
    if not ptype:
        return jsonify({
            "error": "Aucune imprimante detectee a cette adresse. "
                     "Verifie l'IP, que la machine est allumee et sur le reseau "
                     "(Moonraker port 7125, ou OctoPrint avec cle API)."
        }), 422

    # Normalise l'hote (sans schema ni chemin) pour la webcam.
    clean = host
    for pre in ("http://", "https://"):
        if clean.startswith(pre):
            clean = clean[len(pre):]
    clean = clean.split("/")[0]

    printer = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "host": clean,
        "type": ptype,
        "base_url": base,
        "apikey": apikey,
        "webcam": webcam,
    }
    printers = load_printers()
    printers.append(printer)
    save_printers(printers)
    return jsonify({
        "id": printer["id"],
        "name": printer["name"],
        "host": printer["host"],
        "type": ptype,
        "webcam": default_webcam_url(printer),
    })


@app.route("/api/printers/<printer_id>", methods=["DELETE"])
def api_delete(printer_id):
    printers = [p for p in load_printers() if p["id"] != printer_id]
    save_printers(printers)
    _objects_cache.pop(printer_id, None)
    return jsonify({"ok": True})


@app.route("/api/status/<printer_id>")
def api_status(printer_id):
    printer = find_printer(printer_id)
    if not printer:
        return jsonify({"error": "Imprimante inconnue"}), 404
    return jsonify(fetch_status(printer))


def _moonraker_post(base, path):
    requests.post(f"{base}{path}", timeout=HTTP_TIMEOUT).raise_for_status()


def _moonraker_gcode(base, script):
    requests.post(f"{base}/printer/gcode/script", params={"script": script},
                  timeout=HTTP_TIMEOUT).raise_for_status()


@app.route("/api/control/<printer_id>", methods=["POST"])
def api_control(printer_id):
    """Actions sur l'imprimante (Moonraker) : pause/reprise/annuler/arret/prechauffe/refroidir."""
    printer = find_printer(printer_id)
    if not printer:
        return jsonify({"error": "Imprimante inconnue"}), 404
    if printer.get("type") != "moonraker":
        return jsonify({"error": "Contrôles disponibles uniquement pour Moonraker"}), 422

    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action")
    base = printer["base_url"]

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
                return jsonify({"error": "Matériau inconnu"}), 400
            _moonraker_gcode(base, f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={preset['ext']}")
            _moonraker_gcode(base, f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={preset['bed']}")
        else:
            return jsonify({"error": "Action inconnue"}), 400
    except requests.RequestException as e:
        return jsonify({"error": f"Échec de la commande : {e}"}), 502

    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    s = load_settings()
    return jsonify({"discord_webhook": s.get("discord_webhook", ""),
                    "alerts": s.get("alerts", {}), "appearance": s.get("appearance", {})})


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    data = request.get_json(force=True, silent=True) or {}
    s = load_settings()
    if "discord_webhook" in data:  # chaine vide = suppression
        s["discord_webhook"] = (data.get("discord_webhook") or "").strip()
    if "alerts" in data and isinstance(data["alerts"], dict):
        s["alerts"] = {**s["alerts"], **data["alerts"]}
    if "appearance" in data and isinstance(data["appearance"], dict):
        s["appearance"] = {**s["appearance"], **data["appearance"]}
    save_settings(s)
    return jsonify({"alerts": s["alerts"], "appearance": s["appearance"]})


@app.route("/api/settings/test", methods=["POST"])
def api_test_webhook():
    data = request.get_json(force=True, silent=True) or {}
    # On teste l'URL fournie si presente, sinon celle enregistree.
    webhook = (data.get("discord_webhook") or "").strip() or load_settings().get("discord_webhook")
    if not webhook:
        return jsonify({"error": "Aucun webhook configuré"}), 400
    ok = discord_send(webhook, "🔔 PrintWatch — Test", "Les alertes Discord fonctionnent !", 0x6C8CFF)
    return (jsonify({"ok": True}) if ok else (jsonify({"error": "Envoi échoué"}), 502))


@app.route("/api/stats/<printer_id>")
def api_stats(printer_id):
    printer = find_printer(printer_id)
    if not printer:
        return jsonify({"error": "Imprimante inconnue"}), 404
    return jsonify(fetch_history(printer))


@app.route("/api/stats/<printer_id>/csv")
def api_stats_csv(printer_id):
    """Export CSV (separateur ';' pour Excel FR) de tout l'historique."""
    printer = find_printer(printer_id)
    if not printer or printer.get("type") != "moonraker":
        return Response(status=404)
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["date", "fichier", "statut", "duree_min", "filament_mm", "poids_g", "type"])
    for j in _raw_history(printer["base_url"]):
        nj = _normalize_job(j)
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(nj["end_time"])) if nj["end_time"] else ""
        w.writerow([date, nj["filename"], nj["status"], round((nj["duration"] or 0) / 60, 1),
                    round(nj["filament"] or 0), nj["weight"] or "", nj["filament_type"] or ""])
    fname = f"printwatch_{printer['name']}.csv".replace(" ", "_")
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@app.route("/api/thumb/<printer_id>")
def api_thumb(printer_id):
    """Proxy d'une miniature de gcode stockee sur l'imprimante (Moonraker)."""
    printer = find_printer(printer_id)
    if not printer or printer.get("type") != "moonraker":
        return Response(status=404)
    path = request.args.get("path", "")
    if not path:
        return Response(status=404)
    url = f"{printer['base_url']}/server/files/gcodes/{path}"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if not r.ok:
            return Response(status=404)
        return Response(r.content, content_type=r.headers.get("Content-Type", "image/png"))
    except requests.RequestException:
        return Response(status=502)


@app.route("/api/webcam/<printer_id>")
def api_webcam(printer_id):
    """Proxy du flux MJPEG (utile si le navigateur bloque le contenu mixte ou le CORS)."""
    printer = find_printer(printer_id)
    if not printer:
        return Response(status=404)
    url = default_webcam_url(printer)

    try:
        upstream = requests.get(url, stream=True, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return Response(status=502)

    content_type = upstream.headers.get("Content-Type", "multipart/x-mixed-replace")

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=4096):
                yield chunk
        except requests.RequestException:
            return

    return Response(stream_with_context(generate()), content_type=content_type)


# --------------------------------------------------------------------------
# Layouts (panneaux libres)
# --------------------------------------------------------------------------
@app.route("/api/layouts", methods=["GET"])
def api_get_layouts():
    return jsonify(load_layouts())


@app.route("/api/layouts", methods=["POST"])
def api_save_layout():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    widgets = data.get("widgets") or []
    if not name:
        return jsonify({"error": "Nom requis"}), 400
    d = load_layouts()
    d["layouts"][name] = widgets
    d["active"] = name
    save_layouts(d)
    return jsonify({"ok": True, "name": name})


@app.route("/api/layouts/<path:name>", methods=["DELETE"])
def api_delete_layout(name):
    d = load_layouts()
    d["layouts"].pop(name, None)
    if d.get("active") == name:
        d["active"] = next(iter(d["layouts"]), "")
    save_layouts(d)
    return jsonify({"ok": True})


@app.route("/api/layouts/active", methods=["POST"])
def api_set_active_layout():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "")
    d = load_layouts()
    d["active"] = name
    save_layouts(d)
    return jsonify({"ok": True})


if __name__ == "__main__":
    threading.Thread(target=monitor_loop, daemon=True).start()
    print("PrintWatch demarre sur http://localhost:8088")
    app.run(host="0.0.0.0", port=8088, threaded=True)
