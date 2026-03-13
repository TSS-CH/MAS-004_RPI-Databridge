# SUPPORT_CHANGELOG - MAS-004_RPI-Databridge

## 2026-03-13 (ZBC Library Integration)
- Added `MAS-004_ZBC-Library` as a new managed subproject.
- Extended multi-repo status/sync scripts to include the ZBC library.
- Added bundle-based Pi synchronization for repos without a central Git remote.

## 2026-03-13 (TTO Workbook Mapping via Live CurrentParameters)
- Extended `MAS-004_ZBC-Library` with a CLARiTY parameter-archive parser and a `request_current_parameters()` helper.
- Verified live against the 6530 that `FRQ[CURRENT_PARAMETERS]` returns `CurrentParameters.xml`.
- Updated `..\Parameterliste SAR41-MAS-004_V11.11.25.xlsx`:
  - added a `ZBC Mapping:` column
  - repaired `TTP00055` as `TextCommsAsyncNotificationsEnabled`
  - added new TTO parameters `TTP00064` .. `TTP00072`
- Added the reusable workbook updater:
  - `..\MAS-004_ZBC-Library\tools\update_tto_workbook.py`
- Fixed a PowerShell parsing bug in `scripts/mas004_multirepo_sync.ps1`:
  - bundle-sync `scp` target now uses `${resolvedSshHost}` correctly
  - without this, TEST sync for repos without a central remote aborted before transfer
- Fixed bundle-sync behavior for missing remote paths:
  - `MAS-004_ZBC-Library` can now be created on the Pi from the local bundle when `/opt/MAS-004_ZBC-Library` does not exist yet
- Fixed bundle-sync permissions for first deploy:
  - new repos are cloned in `/tmp` and then moved into `/opt/...` via `sudo`
  - this avoids `Permission denied` when the target folder does not yet exist

## 2026-03-13 (Configurable Forwarding Ports + ESP Port 3010)
- TCP forwarding no longer hardcodes device main ports.
  - listeners now follow the configured device ports: `esp_port`, `vj3350_port`, `vj6530_port`
  - this fixes the mismatch where the UI showed `ESP = 3010` but the runtime still listened on `3009`
- Hardened `mas004_rpi_databridge/tcp_forwarder.py` for parallel traffic:
  - shorter upstream connect timeout
  - larger socket buffers
  - `TCP_NODELAY` / keepalive
  - bidirectional pump threads per connection instead of one shared select/send loop
  - active connection tracking and cleaner shutdown on reconcile/restart
- Updated Settings UI text to describe configured main ports plus extra routed ports.
- Fixed ESP line-response parsing in `mas004_rpi_databridge/device_clients.py`:
  - only the first received line is now treated as the response payload
  - prevents heartbeat or extra trailing lines from corrupting `MAP`/`MAS` reads
- Fixed a forwarding regression in `mas004_rpi_databridge/tcp_forwarder.py`:
  - listener sockets no longer get a read timeout
  - this keeps the accept loop alive and fixes hanging routed ports such as `10.27.67.69:3010`
- Added active ESP push ingestion on the Raspi:
  - new listener `mas004_rpi_databridge/esp_push_listener.py`
  - binds on `eth1_ip:esp_port` when `esp_simulation=false`
  - accepts device-origin `MA*` lines, persists them locally and forwards them to Microtom via outbox
- Moved operation-line parsing into `mas004_rpi_databridge/protocol.py` so router and ESP-push path use the same syntax rules.

## 2026-03-04
- Added persistent support context files:
  - `docs/PROJECT_CONTEXT.md`
  - `docs/SUPPORT_RUNBOOK.md`
  - `docs/SUPPORT_CHANGELOG.md`
- Added multi-repo operations scripts:
  - `scripts/mas004_multirepo_status.ps1`
  - `scripts/mas004_multirepo_sync.ps1`
- Established policy: this repository is the main/orchestration project.
- Baseline local HEAD during this entry: `af82b02`.
- Added current sync snapshot:
  - Main repo synced on Pi.
  - Three Pi subproject repos still dirty and behind 1 (safe-skip mode).

## 2026-03-04 (Pi Safe Cleanup + Full Sync)
- Performed safe cleanup on Pi for all 3 subprojects:
  - Created backup branches and committed tracked local changes before sync.
  - Fast-forwarded `main` to `origin/main`.
- Backup branches created on Pi:
  - `MAS-004_ESP32-PLC-Bridge`: `backup/pi-pre-sync-mas-004_esp32-plc-bridge-20260304-083407`
  - `MAS-004_VJ3350-Ultimate-Bridge`: `backup/pi-pre-sync-mas-004_vj3350-ultimate-bridge-20260304-083551`
  - `MAS-004_VJ6530-ZBC-Bridge`: `backup/pi-pre-sync-mas-004_vj6530-zbc-bridge-20260304-083601`
- Added local Git excludes on Pi subprojects for runtime artifacts:
  - `.venv/`, `*.egg-info/`, `__pycache__/`, `**/__pycache__/`
- Result:
  - Local + Pi + Git are now fully synchronized for all 4 repositories.
  - All 4 systemd services on Pi are active.

## 2026-03-04 (Parallel Microtom Target)
- Added optional secondary Microtom target in config:
  - `peer_base_url_secondary`
- Extended outbound enqueue/routing to fan out to both configured targets:
  - primary `peer_base_url`
  - optional `peer_base_url_secondary`
- Added sender behavior for secondary target as best-effort:
  - failed sends to secondary are dropped (no retry backlog), to protect primary channel latency.
- Updated Settings UI (`/ui/settings`) to edit secondary peer URL.
- Updated default/example config files and project context docs.

## 2026-03-04 (TEST/LIVE Deployment Profiles)
- Added deployment target profile helper:
  - `scripts/mas004_deploy_targets.ps1`
- Updated multi-repo scripts to support `-Target test|live`:
  - `scripts/mas004_multirepo_status.ps1`
  - `scripts/mas004_multirepo_sync.ps1`
- TEST (`10.27.67.69`) is now default target for status/sync.
- LIVE (`192.168.1.20`) is blocked by default and requires:
  - `-Target live -AllowLive`
- Added unreachable-target handling so TEST sync can run safely even while test device is not connected.
- Added optional environment variable overrides for host/web target metadata.

## 2026-03-04 (NTP + TCP Relay for Device Ports)
- Added NTP configuration and runtime sync loop:
  - config keys: `ntp_server`, `ntp_sync_interval_min`
  - runtime worker: `mas004_rpi_databridge/ntp_sync.py`
  - Settings UI fields and config API mapping updated.
- Added TCP relay service from Raspi `eth0` to device hosts on `eth1`:
  - initial relay ports: `3007` (VJ6530), `3008` (VJ3350), `3009` (ESP32)
  - optional extra relay ports per device: `esp_forward_ports`, `vj3350_forward_ports`, `vj6530_forward_ports`
  - runtime worker: `mas004_rpi_databridge/tcp_forwarder.py`
  - started from `service.py` at app startup.
- NTP robustness fix:
  - `ntp_sync.py` now searches binaries also in `/usr/sbin`/`/sbin`, so `ntpdate` is detected in systemd service context.

## 2026-03-04 (TEST Raspi Setup Finalization + Deploy Hardening)
- Finalized TEST Raspi setup on `10.27.67.69`:
  - `eth0`: `10.27.67.69/24`, gateway `10.27.67.1`, DNS `10.28.193.4 10.27.30.201`
  - `eth1`: `192.168.2.100/24` without gateway
  - timezone set to `Europe/Zurich`
- Root-cause fixed for "new code not active after pull/install":
  - stale `build/` artifacts on Pi caused old package content to be reinstalled.
  - verified by missing `/api/ui/status/public` and absent `[NTP]/[FWD]` runtime logs.
- Deployment hardening:
  - `scripts/mas004_multirepo_sync.ps1` now performs on remote (when `-RestartServices` is used):
    - `rm -rf build`
    - `.venv/bin/python -m pip install --no-cache-dir --force-reinstall .`
    - service restart
- Verified runtime after reinstall:
  - endpoint `GET /api/ui/status/public` returns `200`
  - forwarding listeners active for the configured device ports
  - NTP sync successful against `10.27.30.201`

## 2026-03-12 (Boot Robustness Fixes)
- Fixed TEST/LIVE runtime behavior after reboot:
  - TCP forwarders now reconcile every 5 seconds and retry binds after `eth0` becomes available.
  - This prevents the boot race where `mas004-rpi-databridge` started before `eth0` had carrier and forwarding listeners stayed down.
- Improved NTP sync behavior:
  - command detection now uses explicit executable checks
  - failed sync attempts now report the real command error instead of the misleading "No supported NTP client found"
  - after a failed sync, retry happens after 15 seconds instead of waiting the full configured interval
- Hardened Pi package reinstall in `scripts/mas004_multirepo_sync.ps1`:
  - uses `--no-deps --no-build-isolation`
  - avoids dependency downloads during deploy, which is important when the Pi clock is wrong before first NTP sync

## Maintenance Rule
- Add one entry for every change that affects:
  - architecture
  - deployment flow
  - API contracts
  - multi-repo sync behavior

