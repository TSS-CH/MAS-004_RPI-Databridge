# MAS-004_RPI-Databridge QuickStart

**Dokumentversion:** 3.0  
**Softwarestand:** MAS-004_RPI-Databridge `0.3.0`  
**Autor:** Erwin Egli  
**Datum:** 2026-02-19

## 1. Ziel
In 10-15 Minuten eine funktionsfähige Testverbindung aufbauen zwischen:

1. Raspi Databridge (`192.168.210.20:8080`)
2. Microtom Server/Simulator (`192.168.210.10:9090`)

## 2. Aktuelle URLs
1. Home: `https://192.168.210.20:8080/`
2. Test UI: `https://192.168.210.20:8080/ui/test`
3. Settings UI: `https://192.168.210.20:8080/ui/settings`
4. API Docs: `https://192.168.210.20:8080/docs`
5. Raspi Health: `https://192.168.210.20:8080/health`

## 3. UI-Schnellüberblick

### Home
![Home UI](screenshots/ui_home.png)

### Settings
![Settings UI](screenshots/ui_settings.png)

### Test UI
![Test UI](screenshots/ui_test.png)

## 4. Minimal-Setup auf Raspi

## 4.1 Service prüfen
```bash
sudo systemctl status mas004-rpi-databridge.service
```

## 4.2 Peer korrekt setzen
In `Settings -> Databridge / Microtom` müssen passen:

1. `peer_base_url = https://192.168.210.10:9090`
2. `peer_watchdog_host = 192.168.210.10`
3. `peer_health_path = /health`

Dann `Save Bridge + Restart`.

## 4.3 Shared Secret
1. Falls aktiv, denselben Wert in Microtom verwenden.
2. Falls leer, ist Secret-Prüfung deaktiviert.

## 5. Minimal-Setup auf Microtom-Seite
Microtom muss bereitstellen:

1. `GET /health` -> 2xx
2. `POST /api/inbox` -> nimmt Callback an und gibt 2xx

## 6. Schnelltest End-to-End

## 6.1 Von Microtom an Raspi senden
```bash
curl -k -X POST "https://192.168.210.20:8080/api/inbox" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: qs-0001" \
  -H "X-Shared-Secret: <SECRET>" \
  -d "{\"cmd\":\"TTP00002=?\",\"source\":\"microtom\"}"
```

Erwartung (sofort):
```json
{"ok":true,"stored":true,"idempotency_key":"qs-0001"}
```

Erwartung (asynchron):
Microtom erhält später Callback auf `POST /api/inbox`.

## 6.2 Aus Test-UI senden
1. `https://192.168.210.20:8080/ui/test` öffnen.
2. Im Feld **VJ6530** z. B. senden:
   1. `TTP00002=23`
   2. oder mehrere: `TTP00002=23, TTP00003=10, TTP00004=11`
3. In Logs prüfen, ob Weiterleitung zu Microtom erfolgt.

## 7. Wichtige API-Endpunkte (Kurzfassung)

### Öffentlich
1. `GET /health`
2. `POST /api/inbox`
3. `GET /api/ui/status/public`
4. UI-Seiten (`/`, `/docs`, `/ui/params`, `/ui/settings`, `/ui/test`)

### Token-geschützt (`X-Token`)
1. `GET/POST /api/config`
2. `GET/POST /api/system/network`
3. `POST /api/test/send`
4. `POST /api/params/import`
5. `GET /api/params/list`
6. `POST /api/params/edit`
7. `GET/POST /api/ui/logs*`
8. `GET /api/logfiles/*`

Vollständig und detailliert: `docs/Microtom_Interface.md`

## 8. Zertifikat ohne Browser-Warnung
Nach IP-Wechsel muss Zertifikat neu erzeugt und importiert werden.

### 8.1 Auf Raspi neues Zertifikat erzeugen
```bash
sudo openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 825 \
  -keyout /etc/mas004_rpi_databridge/certs/raspi.key \
  -out /etc/mas004_rpi_databridge/certs/raspi.crt \
  -subj "/CN=192.168.210.20" \
  -addext "subjectAltName=IP:192.168.210.20,IP:127.0.0.1,DNS:raspberrypi"
sudo systemctl restart mas004-rpi-databridge.service
```

### 8.2 Zertifikat nach Windows kopieren
```powershell
scp pi@192.168.210.20:/etc/mas004_rpi_databridge/certs/raspi.crt "$env:USERPROFILE\Downloads\raspi.crt"
```

### 8.3 In Root-Store importieren (Admin-PowerShell)
```powershell
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\raspi.crt" -CertStoreLocation Cert:\LocalMachine\Root
```

## 9. Schnell-Diagnose

### 9.1 Outbox prüfen
```bash
sudo sqlite3 /var/lib/mas004_rpi_databridge/databridge.db \
"SELECT id,url,retry_count,datetime(next_attempt_ts,'unixepoch','localtime') FROM outbox ORDER BY id;"
```

### 9.2 Service-Log prüfen
```bash
sudo journalctl -u mas004-rpi-databridge.service -n 200 --no-pager
```

### 9.3 Häufigste Ursachen
1. Alte/falsche Peer-URL in Outbox (falsche IP).
2. Shared-Secret stimmt nicht.
3. Microtom `/health` nicht erreichbar.
4. Token fehlt für geschützte API-Aufrufe.

## 10. Release-Notiz
1. **v3.0 (2026-02-19):** QuickStart auf `192.168.210.x` aktualisiert, UI-Screenshots, TLS-Zertifikatsablauf, API-Kurzreferenz und Troubleshooting erweitert.
