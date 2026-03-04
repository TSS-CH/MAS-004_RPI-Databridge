# SUPPORT_CHANGELOG - MAS-004_RPI-Databridge

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

## Maintenance Rule
- Add one entry for every change that affects:
  - architecture
  - deployment flow
  - API contracts
  - multi-repo sync behavior

