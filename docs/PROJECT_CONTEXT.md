# PROJECT_CONTEXT - MAS-004_RPI-Databridge

## Role
- Main project in the MAS-004 landscape.
- Runs on Raspberry PLC and provides the central data bridge to Microtom.
- Hosts the operator web UI and the test environment.
- Coordinates simulated/real sub-bridges for ESP32, VJ3350, VJ6530.

## Repository Scope
- Core app package: `mas004_rpi_databridge/`
- Web/API: `mas004_rpi_databridge/webui.py`
- Message reliability: `inbox.py`, `outbox.py`, `router.py`, `service.py`, `http_client.py`, `watchdog.py`
- Background ops: `ntp_sync.py` (periodic time sync), `tcp_forwarder.py` (eth0->eth1 TCP relay)
- Parameter engine: `params.py`, `params_store.py`, `protocol.py`, `device_bridge.py`
- Networking helper: `netconfig.py`
- Deployment: `systemd/mas004-rpi-databridge.service`, `scripts/`

## Runtime Contracts
- Local API/UI endpoint: `https://<raspi-ip>:8080`
- Main inbound endpoint for Microtom: `POST /api/inbox`
- Main outbound callback target: `<peer_base_url>/api/inbox`
- Optional parallel outbound callback target: `<peer_base_url_secondary>/api/inbox`
- Health endpoint: `GET /health`
- TCP relay endpoints on Raspi eth0:
  - main relay ports follow the configured device ports: `vj6530_port`, `vj3350_port`, `esp_port`
  - optional extra per-device ports from Settings UI (`*_forward_ports`)
  - current TEST setup uses `esp_port = 3010`

## Deployment Topology
- TEST Raspberry:
  - SSH: `pi@10.27.67.69`
  - UI/API: `https://10.27.67.69:8080`
  - Policy: default sync target (always keep aligned with local + git)
- LIVE Raspberry:
  - SSH: `pi@192.168.1.20`
  - UI/API: `https://192.168.210.20:8080`
- Policy: update only on explicit release command
- Script target metadata can be overridden via environment variables:
  - `MAS004_TEST_SSH`, `MAS004_LIVE_SSH`, `MAS004_TEST_WEB`, `MAS004_LIVE_WEB`

## Persistent Paths
- Config: `/etc/mas004_rpi_databridge/config.json`
- DB: `/var/lib/mas004_rpi_databridge/databridge.db`

## Systemd Service
- `mas004-rpi-databridge.service`
- Working dir on Pi: `/opt/MAS-004_RPI-Databridge`

## Priority Rule
- This repo is the orchestration authority.
- Subprojects must be treated as extensions of this repo, not peers.
- All multi-repo operations should start here.

## Multi-Repo Dependency Map
- `MAS-004_ESP32-PLC-Bridge`: ESP32 transport/probe subproject
- `MAS-004_VJ3350-Ultimate-Bridge`: VJ3350 transport/probe subproject
- `MAS-004_VJ6530-ZBC-Bridge`: VJ6530 transport/probe subproject

## Sync/Support Policy
- Before and after changes in this repo, run:
  - `scripts/mas004_multirepo_status.ps1 -Target test`
  - `scripts/mas004_multirepo_sync.ps1 -Target test -RestartServices`
- Never use destructive git commands on Pi repos.
- If a Pi repo is dirty, do not auto-overwrite; report and require explicit decision.
- LIVE deployment requires explicit opt-in:
  - `scripts/mas004_multirepo_sync.ps1 -Target live -AllowLive -RestartServices`

## Last Reviewed
- Date: 2026-03-13
- Local HEAD baseline during creation: `af82b02`

## Current Sync Snapshot (2026-03-04)
- Local git + all 4 repos: synchronized and clean.
- Remote status depends on selected target profile (`test` or `live`) and connectivity.
- Safety policy remains active: no destructive overwrite on dirty Pi trees.
