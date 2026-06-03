# 🖨️ PrintWatch — Dashboard universel d'imprimantes 3D

Monitore tes imprimantes 3D (Voron, Creality K1/K-series, Ender V3, Elegoo Neptune…)
depuis un seul dashboard web moderne. **Tu mets l'IP, le protocole est détecté
automatiquement.** Backend Python léger + interface web temps réel.

![Python](https://img.shields.io/badge/python-3.9+-blue) ![Flask](https://img.shields.io/badge/flask-3.0-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

## ✨ Fonctionnalités

### 📊 Tableau de bord temps réel
- **Détection automatique** du protocole à partir de l'IP (Moonraker / OctoPrint)
- État + **progression** (anneau animé), nom du fichier, temps restant
- **Températures** buse / plateau / chambre avec graphique temps réel
- **Webcam** (flux MJPEG proxifié — pas de souci CORS / contenu mixte)
- **Santé machine** : CPU %, température CPU, RAM, uptime + sondes (MCU, Cartographer…)
- **Contrôles** : pause / reprise / annuler / arrêt d'urgence / préchauffe / refroidir

### 📈 Statistiques
- KPIs cumulés : nombre d'impressions, temps total, filament total, taux de réussite
- **Graphe d'activité** (impressions par jour, 30 jours)
- **Filament par type** (PLA/PETG/ABS…) + estimation du **coût** (€/kg)
- Historique des impressions avec **miniatures**, détail au clic
- **Export CSV** de tout l'historique

### 🎛️ Panneaux libres (constructeur de dashboards)
- Grille **glisser-déposer / redimensionnable** de widgets
- Widgets : Progression, Températures, Santé, Graphe, Webcam, Contrôles
- **Plusieurs layouts** nommés, sauvegardés côté serveur

### 🔔 Alertes Discord
- Notification à la **fin d'impression**, en cas d'**erreur**, ou de **déconnexion**
- Surveillance en arrière-plan via webhook Discord

### 🎨 Interface entièrement personnalisable
- **Thème** clair / sombre, **PWA installable**, **mode TV** plein écran
- Nom + logo, couleurs d'accent (+ presets), police, arrondi, densité
- **Mode performance** (animations réduites) pour les machines modestes
- Réorganisation des cartes, vue compacte, widgets masquables par carte

## 🔌 Protocoles supportés

| Firmware | Marques typiques | Config requise |
|----------|------------------|----------------|
| **Moonraker** (Klipper) | Voron, Creality K1/K1 Max, Ender-3 V3, Elegoo Neptune (Klipper), Sovol… | Aucune — juste l'IP (port 7125) |
| **OctoPrint** | Toute imprimante Marlin pilotée par un OctoPrint | Clé API OctoPrint |

> Les imprimantes **résine Elegoo (SDCP)** et **Bambu Lab (MQTT)** ne sont pas encore
> gérées. L'architecture est prête pour les ajouter (voir `fetch_status` dans `app.py`).

## 🏗️ Architecture : UI hébergée + agent local

PrintWatch sépare **l'interface** (statique, hébergeable) de **l'agent** (local, qui
parle aux imprimantes). **Toute la configuration est stockée dans le navigateur
(localStorage)** — privée sur ton appareil, rien ne transite par un serveur tiers.

```
   UI (docs/index.html)        CORS        Agent (app.py)           LAN      Imprimantes
   GitHub Pages OU local  ───────────────►  sans état, :8088  ───────────►  (Voron…)
   config en localStorage    localhost      proxy + alertes      Moonraker
```

## 🚀 Installation & usage

```bash
pip install -r requirements.txt
python app.py        # lance l'agent local sur le port 8088
```

Deux façons d'ouvrir l'interface (la même UI) :

1. **En local** → ouvre **http://localhost:8088** (l'agent sert aussi l'UI).
2. **Hébergée** → l'UI sur GitHub Pages se connecte à ton agent local (qui doit tourner).
   Au premier accès, le navigateur peut demander l'accès au réseau local → **autorise**.

Clique sur « + Ajouter une imprimante », entre l'IP et valide. La config (imprimantes,
layouts, apparence, alertes) est sauvée dans **ton navigateur**.

> Agent ailleurs ? Règle son adresse dans ⚙️ Réglages → « Agent local ».

## 🗂️ Structure

```
app.py                     # Agent local : API sans état, proxy webcam, alertes Discord
docs/                      # Racine web (servie en local par l'agent ET par GitHub Pages)
  index.html               #   Frontend complet (dashboard, stats, panneaux, réglages)
  manifest.webmanifest     #   PWA
  sw.js                    #   Service worker
  static/logo.svg          #   Logo
requirements.txt           # flask, requests
```

Aucune donnée n'est versionnée : tout vit dans le **localStorage du navigateur**
(le webhook Discord est poussé à l'agent en mémoire, jamais écrit sur disque).

## ⚙️ Notes

- **Sécurité** : pas d'authentification — à n'exposer que sur ton réseau local.
- Le serveur écoute sur `0.0.0.0:8088` (accessible depuis le réseau local).
- Après un **arrêt d'urgence**, Klipper nécessite un `FIRMWARE_RESTART` (comportement de sécurité normal).

## 🛠️ Ajouter un nouveau protocole

1. Écris `fetch_<protocole>(printer, base)` renvoyant le format normalisé (voir `empty_status()`).
2. Ajoute la détection dans `detect_protocol()`.
3. Branche-la dans `fetch_status()`.

## 📄 Licence

MIT
