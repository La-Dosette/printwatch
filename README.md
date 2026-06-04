# 🖨️ PrintWatch — Dashboard universel d'imprimantes 3D

Monitore tes imprimantes 3D (Voron, Creality K1/K-series, Ender V3, Elegoo Neptune…)
depuis un seul dashboard web moderne. **Tu mets l'IP, le protocole est détecté
automatiquement.** Backend Python léger + interface web temps réel.

![Python](https://img.shields.io/badge/python-3.9+-blue) ![Flask](https://img.shields.io/badge/flask-3.0-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

## ✨ Fonctionnalités

### 📊 Tableau de bord temps réel
- **Détection automatique** du protocole à partir de l'IP (Moonraker / OctoPrint / FlashForge 5M)
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

## 🔌 Connecteurs / compatibilité

PrintWatch vise une approche **universelle par connecteurs** : chaque marque expose
un protocole différent, donc l'agent détecte puis normalise les données vers le même
format UI (état, progression, températures, webcam, santé, contrôles si possible).

| Connecteur | Marques / machines typiques | Statut | Config requise |
|------------|-----------------------------|--------|----------------|
| **Moonraker / Klipper** | Voron, Creality K1/K1 Max rootées ou compatibles, Ender-3 V3, **Elegoo Neptune 4 / Pro / Plus / Max**, Sovol… | ✅ Monitoring + contrôles + stats | IP uniquement (`7125`) |
| **Creality K1/K1 Max caméra seule** | K1/K1 Max quand Moonraker est fermé mais caméra locale active | ✅ Webcam uniquement | IP ou `http://IP`, auto-détection `:8080/?action=stream` |
| **OctoPrint** | Marlin via Raspberry/OctoPrint | ✅ Monitoring | IP + clé API OctoPrint |
| **FlashForge Adventurer 5M / 5M Pro** | FlashForge AD5M / AD5M Pro en LAN | ✅ Monitoring de base | IP + `serialNumber` + `checkCode` (`8898`) |
| **Elegoo SDCP FDM** | **Centauri Carbon / Centauri Carbon 2** | ✅ Monitoring de base | IP (`3030` WebSocket, webcam `3031/video`) |
| **Bambu Lab MQTT LAN** | X1C/X1E, P1S/P1P, A1/A1 Mini, P2S… | ✅ Monitoring de base | IP + `serialNumber` + LAN Access Code (`8883`) |
| **Creality LAN natif** | Creality Hi / K2 / certains K1 non-Moonraker | 🟡 À brancher | WebSocket local `9999` selon modèle |
| **Elegoo SDCP résine** | Mars / Saturn / Jupiter récents | 🟡 Plus tard | SDCP v3 / WebSocket + découverte |
| **Anycubic** | Kobra / Photon récents | 🟡 Variable | Rinkhals/Moonraker recommandé ; API locale officielle souvent fermée |
| **Bambu Lab** | P/X/A series | 🟡 À brancher | MQTT LAN + access code |

Le principe : si une marque n'a pas d'API locale ouverte, PrintWatch affichera un
message clair au lieu de faire semblant. Les connecteurs sont ajoutés un par un dans
`app.py` (`detect_protocol`, `fetch_<connecteur>`, puis branchement dans `fetch_status`).

### Creality K1 / K1 Max

Deux cas existent :

- **Moonraker ouvert** (`http://IP:7125`) : PrintWatch récupère état, progression,
  températures, historique et peut envoyer les contrôles Klipper.
- **Moonraker fermé / firmware stock** : PrintWatch tente quand même la caméra locale
  (`http://IP:8080/?action=stream`, `/webcam/?action=stream`, ports Fluidd/Mainsail).
  Dans ce mode, la carte est ajoutée en **caméra seule**.

Tu peux entrer `192.168.x.x` ou `http://192.168.x.x`. Si une URL complète avec port
fonctionne mieux chez toi, colle-la directement dans le champ IP/hôte ou dans le champ
URL webcam.

### Bambu Lab

Pour Bambu Lab, active le mode LAN / Developer Mode côté imprimante puis renseigne :

- **IP** : adresse locale de l'imprimante ;
- **Serial / identifiant** : numéro de série ;
- **Code / clé API** : LAN Access Code.

Le monitoring passe par MQTT TLS local (`8883`). La caméra Bambu n'est pas affichée par
défaut dans l'interface car elle n'est pas un flux MJPEG simple ; tu peux quand même
renseigner une URL manuelle si tu utilises un proxy caméra compatible.

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

### Windows : double-clic

Le plus simple :

1. Double-clique sur `start-printwatch.bat`.
2. Le script cree un environnement Python local `.venv` si besoin.
3. Il installe les dependances au premier lancement.
4. Il ouvre l'interface hebergee : https://la-dosette.github.io/printwatch/
5. Il garde l'agent local actif sur `http://localhost:8088`.

Garde la fenetre ouverte tant que tu utilises PrintWatch.

### Windows : executable sans console

Pour generer un vrai `.exe` qui ouvre l'interface et lance l'agent sans fenetre CMD :

```powershell
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
```

Le fichier final sera :

```text
dist\PrintWatchAgent.exe
```

Double-clique dessus : l'agent demarre en arriere-plan et ouvre
https://la-dosette.github.io/printwatch/.

Une icone **PrintWatch Agent** apparait dans la zone de notification Windows
(la petite fleche pres de l'horloge). Son menu permet de :

- ouvrir l'interface hebergee ;
- ouvrir l'agent local (`http://localhost:8088`) ;
- quitter l'agent a tout moment.

Si l'agent est deja actif, un deuxieme lancement ouvre simplement l'interface et quitte.

Les logs sont ecrits ici en cas de souci :

```text
%LOCALAPPDATA%\PrintWatch\agent.log
```

### Manuel

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
