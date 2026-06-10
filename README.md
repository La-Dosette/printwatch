# PrintWatch

Simple local dashboard for 3D printers.

PrintWatch runs a small Python agent on your PC. The web UI talks to that agent and keeps all settings in your browser.

## What It Does

- Adds a printer from an IP address.
- Detects Moonraker, OctoPrint, PrusaLink, Duet/RRF, FlashForge 5M, Elegoo SDCP FDM, Bambu Lab MQTT, Creality LAN candidates, Anycubic candidates, Repetier candidates, or webcam-only mode.
- Shows status, progress, temperatures, webcam, basic stats, and controls when supported.
- Sends optional Discord alerts.

## Universal Protocol Model

PrintWatch detects a printer family from an IP address, then exposes explicit
capabilities to the UI:

- `monitoring`
- `webcam`
- `controls`
- `stats`
- `needs_credentials`
- `actions`, for example `pause`, `resume`, `cancel`, `preheat`, `cooldown`,
  `light_on`, `light_off`, `estop`

The UI only shows vendor controls when the agent declares the action supported.
See [docs/protocol-research.md](docs/protocol-research.md) for the connector
roadmap and protocol notes.

## Quick Start

On Windows, double-click:

```text
start-printwatch.bat
```

Manual start:

```bash
pip install -r requirements.txt
python app.py
```

Open:

```text
http://localhost:8088
```

## Notes

- Keep the agent running while using the dashboard.
- Do not expose the agent to the public internet.
- Printer settings stay in browser localStorage.
- See `UNIVERSALITY_RESEARCH.md` for the protocol roadmap and sources used to expand printer support.

## Build Windows EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
```

Output:

```text
dist\PrintWatchAgent.exe
```

## Project Layout

```text
app.py              Local Flask agent and API
connectors.py       Printer status connectors
docs/index.html     Web UI
printwatch_agent.py Windows tray entry point
requirements.txt    Python dependencies
```

## License

MIT
