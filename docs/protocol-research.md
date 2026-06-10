# PrintWatch Universality Research

Goal: make PrintWatch useful for the widest possible set of LAN-connected 3D
printers without hard-coding one exact model.

The stable product shape is:

- detect the protocol/family from an IP address;
- expose capability flags: `monitoring`, `controls`, `stats`, `webcam`;
- expose supported control actions through `capabilities.actions`;
- use safe common controls first: `pause`, `resume`, `cancel`, `preheat`,
  `cooldown`, `light_on`, `light_off`, `estop`;
- keep vendor-specific actions behind protocol-specific capability checks.

## Current Protocol Map

| Family | Local protocol | Status | Controls | Notes |
| --- | --- | --- | --- | --- |
| Moonraker / Klipper | HTTP API on 7125 | implemented | implemented | Best supported path today. |
| OctoPrint | REST API under `/api` | implemented | candidate | Needs API key and command endpoints wired safely. |
| PrusaLink | HTTP API `/api/v1` | partial | candidate | Needs API key. Confirm exact command endpoints before enabling. |
| Duet / RepRapFirmware | HTTP API + G-code | partial | candidate | RRF is strongly G-code oriented. |
| FlashForge 5M / AD5X | HTTP 8898, TCP 8899 | partial | candidate | Needs serial number and checkCode. |
| Elegoo SDCP FDM | WebSocket 3030, MJPEG 3031 | partial | candidate | SDCP exposes pause/resume/stop command IDs. |
| Bambu Lab | MQTT TLS 8883 | partial | candidate | Needs serial and LAN/developer access code. |
| Creality LAN | model-dependent LAN ports + MJPEG | camera/candidate | unknown | Treat as probe + camera until parser is confirmed. |
| Anycubic LAN | model/firmware-dependent | candidate | unknown | Needs community captures before enabling controls. |
| Camera-only | MJPEG/HTTP stream | implemented | no | Important fallback for closed/proprietary printers. |

## Extracted Facts

### OctoPrint REST API

- Job control uses `POST /api/job` with `start`, `cancel`, `restart`, and
  `pause` plus `action=pause|resume|toggle`.
- Printer status is available through `GET /api/printer`.
- Temperature and raw G-code commands are available through printer command
  endpoints, including arbitrary commands.
- Sources:
  - https://docs.octoprint.org/en/main/api/job.html
  - https://docs.octoprint.org/en/main/api/printer.html

### Bambu Lab Local Mode

- Local MQTT is on `mqtt://{PRINTER_IP}:8883` with TLS, user `bblp`, and LAN
  access/developer code as password.
- Topics are `device/{DEVICE_ID}/report` and `device/{DEVICE_ID}/request`.
- Useful status fields include `mc_percent`, `mc_remaining_time`,
  `gcode_state`, `nozzle_temper`, `bed_temper`, `chamber_temper`, AMS data,
  layer counts, Wi-Fi signal, and lights.
- Candidate controls include `print.pause`, `print.resume`, `print.stop`,
  `print.gcode_line`, and `system.ledctrl`.
- Source:
  - https://github.com/Doridian/OpenBambuAPI/blob/5fc53ba61c7eebbe5f78ebdf83dac840f2761cf5/mqtt.md

### Elegoo Centauri / SDCP FDM

- Discovery can use UDP broadcast port 3000 with message `M99999`.
- Control/status WebSocket is typically `ws://{MainboardIP}:3030/websocket`.
- Video stream can be returned as `http://{ip}:3031/video`.
- Status fields include nozzle, bed, chamber, current/target temperatures,
  current layer, total layer, ticks, filename, and machine/print states.
- Candidate controls include pause `Cmd 129`, stop `Cmd 130`, and continue
  `Cmd 131`.
- Source:
  - https://github.com/OpenCentauri/OpenCentauri/blob/87d097c5ebdc834e201f04a6853acfa95bcc4532/docs/software/api.md

### FlashForge 5M / AD5X

- Modern HTTP API uses port 8898 with JSON and auth by `serialNumber` plus
  `checkCode`.
- Useful endpoints include `/detail`, `/control`, `/product`, `/uploadGcode`,
  `/printGcode`, and `/gcodeThumb`.
- Legacy/low-level TCP uses port 8899 with commands such as `~M115`, `~M105`,
  and `~M27`.
- Source:
  - https://github.com/GhostTypes/ff-5mp-api-ts/blob/2ea04ff6864bde4c45cc46eb3622977ddb13ac6d/docs/protocols.md

### Duet / RepRapFirmware

- RRF follows a "GCode everywhere" model, which makes a small common G-code
  adapter realistic for preheat/cooldown and basic commands after status/auth
  is confirmed.
- Source:
  - https://docs.duet3d.com/en/User_manual/Reference/Gcodes

## Next Connector Waves

1. Generalize the control API around capability/action mapping:
   `pause`, `resume`, `cancel`, `cooldown`, `preheat`, `light_on`,
   `light_off`, `estop`. Each connector declares exactly which actions it
   supports.
2. Add discovery beyond single-IP probing:
   mDNS/Bonjour names; UDP broadcast for Elegoo SDCP; quick port probes for
   `7125`, `80`, `5000`, `3030`, `3031`, `8080`, `8081`, `8883`, `8898`,
   `8899`, `4408`, `4409`; camera URL probes with explicit user override.
3. Upgrade candidates safely:
   enable PrusaLink controls only after confirming official endpoints and
   permissions; enable Duet controls through conservative G-code commands;
   add Bambu MQTT controls with QoS and clear credential warnings; add Elegoo
   SDCP controls with command acknowledgements; add FlashForge controls after
   validating `/control` payload names against real captures or library tests.
4. Create a community capture workflow:
   user exports anonymized status/control responses; PrintWatch stores
   fixtures under `tests/fixtures/<protocol>/`; connector parsers are tested
   against captures before a protocol is marked as supported.
5. Keep the universal fallback strong:
   if a printer cannot be controlled, still show camera, online/offline,
   manual URL, and notes about missing credentials/protocol.
