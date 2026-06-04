"""
Entry point Windows pour PrintWatch Agent.

Ce fichier sert a produire un .exe sans console avec PyInstaller :
- demarre l'agent Flask local sur http://localhost:8088
- ouvre l'interface hebergee sur GitHub Pages
- n'ecrit rien en console ; les erreurs vont dans %LOCALAPPDATA%/PrintWatch/agent.log
"""

import logging
import os
import socket
import threading
import time
import webbrowser

import pystray
from PIL import Image, ImageDraw

from app import app, monitor_loop


PORT = 8088
UI_URL = "https://la-dosette.github.io/printwatch/"
LOCAL_URL = f"http://localhost:{PORT}/"


def log_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "PrintWatch")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "agent.log")


def setup_logging():
    logging.basicConfig(
        filename=log_path(),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


def port_is_open(host="127.0.0.1", port=PORT):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def open_ui_later(delay=1.0):
    def worker():
        time.sleep(delay)
        webbrowser.open(UI_URL)

    threading.Thread(target=worker, daemon=True).start()


def make_tray_image():
    """Petite icone bitmap : papier Archive + marque noire PrintWatch."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    paper = (243, 240, 233, 255)
    ink = (26, 26, 26, 255)

    d.rectangle((6, 6, 58, 58), fill=paper, outline=ink, width=3)
    d.line((16, 42, 26, 30, 36, 36, 48, 20), fill=ink, width=5)
    d.arc((20, 16, 44, 40), start=205, end=335, fill=ink, width=4)
    d.rectangle((29, 37, 35, 43), fill=ink)
    d.rectangle((18, 48, 46, 52), fill=ink)
    return img


def open_hosted_ui(icon=None, item=None):
    webbrowser.open(UI_URL)


def open_local_ui(icon=None, item=None):
    webbrowser.open(LOCAL_URL)


def quit_agent(icon=None, item=None):
    logging.info("Arret demande depuis la zone de notification.")
    if icon:
        icon.stop()
    # Flask dev server n'offre pas d'arret propre portable depuis un thread.
    os._exit(0)


def run_tray():
    menu = pystray.Menu(
        pystray.MenuItem("Ouvrir l'interface", open_hosted_ui, default=True),
        pystray.MenuItem("Ouvrir l'agent local", open_local_ui),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quitter PrintWatch Agent", quit_agent),
    )
    icon = pystray.Icon("PrintWatchAgent", make_tray_image(), "PrintWatch Agent", menu)
    icon.run()


def run_server():
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


def main():
    setup_logging()

    if port_is_open():
        logging.info("Agent deja actif sur le port %s, ouverture de l'interface.", PORT)
        webbrowser.open(UI_URL)
        return

    logging.info("Demarrage de PrintWatch Agent sur le port %s.", PORT)
    threading.Thread(target=monitor_loop, daemon=True).start()
    threading.Thread(target=run_server, daemon=True).start()
    open_ui_later()

    run_tray()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Erreur fatale de PrintWatch Agent")
        raise
