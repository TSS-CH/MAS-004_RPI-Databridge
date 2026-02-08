# MAS-004 Microtom Interface

Version: 2.0  
Date: 2026-02-08  
System: `MAS-004_RPI-Databridge` <-> Microtom

## 1. Ziel dieser Doku
Diese Datei ist die technische Schnittstellenbeschreibung fuer das Microtom-Team.

Nach Umsetzung dieser Anleitung kann Microtom:

1. Befehle korrekt an den Raspi senden.
2. Asynchrone Antworten vom Raspi empfangen und korrekt zuordnen.
3. Retry, Idempotenz und Watchdog-Verhalten robust behandeln.
4. Die Integration mit dem Microtom-Simulator end-to-end testen.

Die Doku ist absichtlich sehr explizit geschrieben, damit auch Einsteiger sie direkt umsetzen koennen.

## 2. Was ihr implementieren muesst
Microtom braucht zwei technische Rollen gleichzeitig:

1. `HTTP Client`  
   Sendet Befehle an den Raspi (`POST /api/inbox`).
2. `HTTP Server`  
   Nimmt Antworten vom Raspi entgegen (`POST /api/inbox` auf Microtom-Seite) und stellt `GET /health` bereit.

Wichtig: Die Raspi-Antwort auf den ersten Request ist nur eine Empfangsbestaetigung.  
Das fachliche Resultat kommt spaeter asynchron per Callback.

## 3. Architektur und Datenfluss

## 3.1 Rollen
1. `Microtom`
   1. Sendet Kommandos wie `TTP00002=?` oder `MAS0026=20`.
   2. Empfaengt Callback-Nachrichten vom Raspi.
2. `Raspi Databridge`
   1. Speichert eingehende Requests idempotent in `inbox`.
   2. Router verarbeitet den Request und spricht mit dem Zielgeraet (oder Simulation).
   3. Ergebnis wird in `outbox` fuer Microtom-Callback eingeplant.

## 3.2 Ablauf (ein kompletter Zyklus)
1. Microtom sendet `POST https://<RPI-IP>:8080/api/inbox`.
2. Raspi antwortet sofort mit JSON wie `{"ok": true, "stored": true, ...}`.
3. Router verarbeitet den Befehl intern.
4. Raspi sendet Ergebnis an Microtom `POST <peer_base_url>/api/inbox`.
5. Microtom antwortet mit HTTP 2xx.

## 3.3 Sequenzdiagramm (vereinfacht)
```text
Microtom Client                     Raspi Databridge                    Microtom Server
      |                                   |                                   |
      | POST /api/inbox (cmd, idem-key)   |                                   |
      |---------------------------------->|                                   |
      |        200 {stored:true/false}    |                                   |
      |<----------------------------------|                                   |
      |                                   | process + route + device call      |
      |                                   |------------------------------------|
      |                                   | POST /api/inbox (msg, corr-id)     |
      |                                   |----------------------------------->|
      |                                   |               200 OK               |
      |                                   |<-----------------------------------|
```

## 4. Begriffe kurz erklaert (fuer Einsteiger)
1. `Idempotency Key`  
   Eindeutige ID pro Business-Nachricht. Bei Retry immer denselben Key.
2. `Correlation ID`  
   Header im Callback, um Antwort wieder dem urspruenglichen Request zuzuordnen.
3. `Outbox`  
   Persistente Queue fuer Raspi -> Microtom Calls mit Retry.
4. `Watchdog`  
   Prueft Netzwerk/Health. Wenn down, pausiert Raspi den Versand.

## 5. API Docs im Browser (Swagger / OpenAPI)
Der Raspi liefert eine integrierte API-Dokumentation:

1. `https://<RPI-IP>:8080/docs`
2. `https://<RPI-IP>:8080/docs/swagger`

Hinweise:
1. Das ist die beste Quelle fuer aktuelle Request/Response-Schemas.
2. Einige reine UI-Endpunkte sind absichtlich nicht im OpenAPI-Schema.
3. Fuer Microtom sind vor allem die Endpunkte in Kapitel 6 und 7 relevant.

## 6. Raspi API fuer Microtom -> Raspi

## 6.1 `POST /api/inbox` (Pflicht-Endpunkt)
Dieser Endpunkt nimmt Microtom-Kommandos entgegen.

### Request Header
1. `X-Idempotency-Key` (stark empfohlen, praktisch Pflicht)
2. `X-Shared-Secret` (nur wenn im Raspi `shared_secret` gesetzt ist)
3. `Content-Type: application/json` (empfohlen)

### Request Body
Erlaubte Formen:

1. JSON-Objekt mit einem dieser Felder: `msg`, `line`, `text`, `cmd`
2. Plaintext (wird intern als `msg` behandelt)

Beispiel JSON:
```json
{
  "cmd": "TTP00002=?",
  "source": "microtom"
}
```

Beispiel cURL:
```bash
curl -k -X POST "https://<RPI-IP>:8080/api/inbox" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: microtom-20260208-0001" \
  -H "X-Shared-Secret: <SECRET>" \
  -d "{\"cmd\":\"TTP00002=?\",\"source\":\"Microtom\"}"
```

### Success Response
```json
{
  "ok": true,
  "stored": true,
  "idempotency_key": "microtom-20260208-0001"
}
```

Bedeutung:
1. `stored=true`: neue Nachricht angenommen und fuer Verarbeitung gespeichert.
2. `stored=false`: Duplikat, gleicher Idempotency-Key war bereits vorhanden.

### Fehler
1. `401 Unauthorized (shared secret)` wenn Secret am Raspi aktiv ist und Header fehlt/falsch ist.

### Wichtige Verhaltensregel
Wenn das Body-Format keinen auswertbaren Befehl enthaelt, wird die Nachricht zwar gespeichert und spaeter als `done` markiert, aber es kommt kein fachlicher Callback.

## 6.2 `GET /health`
Einfacher Gesundheitscheck fuer Infrastrukturtests.

Response:
```json
{"ok": true}
```

## 6.3 Optional fuer Debug (token-geschuetzt)
Diese Endpunkte sind nicht fuer das normale Microtom-Produktivprotokoll noetig, aber fuer Diagnose hilfreich:

1. `GET /api/inbox/next`
2. `POST /api/inbox/{msg_id}/ack`

Beide brauchen `X-Token` (UI Token).

## 7. Microtom API fuer Raspi -> Microtom Callback
Diese beiden Endpunkte muss Microtom bereitstellen.

## 7.1 `GET /health` (Pflicht)
Wird vom Raspi-Watchdog geprueft (zusammen mit Ping).

Anforderung:
1. Bei gesundem Zustand immer HTTP 2xx liefern.
2. Body frei, z. B.:
```json
{"ok": true}
```

## 7.2 `POST /api/inbox` (Pflicht)
Hierhin liefert der Raspi fachliche Antworten.

### Header vom Raspi
1. `X-Idempotency-Key`: Raspi-Callback-ID (fuer dedupe auf Microtom-Seite verwenden)
2. `X-Correlation-Id`: urspruenglicher Microtom Request-Key
3. `Content-Type: application/json`

### Body vom Raspi
```json
{
  "msg": "TTP00002=16",
  "source": "raspi"
}
```

### Microtom muss tun
1. Nachricht schnell speichern/verarbeiten.
2. Bei Erfolg HTTP 2xx senden.
3. Callback deduplizieren ueber `X-Idempotency-Key`.
4. Antwort per `X-Correlation-Id` dem offenen Request zuordnen.

Wichtig:
Im aktuellen Stand sendet der Raspi bei Callback kein `X-Shared-Secret`.  
Wenn Microtom Auth fuer Callback braucht, muss das aktuell netzwerkseitig gelost werden (oder Raspi-Code erweitert werden).

## 8. Nachrichtenformat (Business Commands)

## 8.1 Grundsyntax
`<PTYPE><PID>=<VALUE>`

Lesen:
`<PTYPE><PID>=?`

Beispiele:
1. `TTP00002=?`
2. `TTP00002=16`
3. `MAS0026=20`
4. `LSW1000=1`

## 8.2 Parser-Regel
Die Raspi-Parserregel akzeptiert:

1. `PTYPE`: genau 3 Buchstaben
2. `PID`: alphanumerisch oder `_`
3. `VALUE`: `?` oder `-?[0-9A-Za-z_.]+`

Konsequenz:
1. Leerzeichen im Value sind nicht erlaubt.
2. Sonderzeichen ausser `_` und `.` sind nicht erlaubt.

## 8.3 Routing nach Prefix
Der Raspi leitet anhand von `PTYPE`:

1. `TT*` -> `vj6530` (TTO)
2. `LS*` -> `vj3350` (Laser)
3. `MA*` -> `esp-plc`
4. Sonst -> `raspi`

## 8.4 PID-Normalisierung
Wenn `PID` nur numerisch ist, wird sie aufgefuellt:

1. `TTP` auf 5 Stellen (`2` -> `00002`)
2. `MAP`, `MAS`, `TTE`, `TTW`, `LSE`, `LSW`, `MAE`, `MAW` auf 4 Stellen

## 9. Antworten und Fehlercodes

## 9.1 Erfolgsantworten
1. Read: `<PKEY>=<value>`
2. Write: `ACK_<PKEY>=<value>`

Beispiele:
1. `TTP00002=16`
2. `ACK_TTP00002=16`

## 9.2 Typische NAK-Antworten
1. `NAK_ReadOnly`
2. `NAK_UnknownParam`
3. `NAK_OutOfRange`
4. `NAK_DeviceDown`
5. `NAK_DeviceComm`
6. `NAK_DeviceBadResponse`
7. `NAK_DeviceRejected`
8. `NAK_UnknownDevice`
9. `NAK_MappingMissing`
10. `NAK_ZBC_<HEXCODE>`
11. `NAK_Ultimate_<CODE>`

Format:
`<PKEY>=NAK_...`

## 10. Zuverlaessigkeit im Detail

## 10.1 Inbound Idempotenz (Microtom -> Raspi)
`/api/inbox` wird ueber `idempotency_key` dedupliziert (UNIQUE in SQLite `inbox`).

Regel:
1. Neuer Business-Request -> neuer Key.
2. Retry desselben Requests -> exakt derselbe Key.

## 10.2 Callback-Korrelation (Raspi -> Microtom)
Der Raspi setzt:

1. `X-Correlation-Id = idempotency_key` des urspruenglichen Microtom-Requests

Damit kann Microtom den Callback sicher dem Originalauftrag zuordnen.

## 10.3 Outbox-Retry (Raspi -> Microtom)
Bei Fehlern (Timeout, Netzwerk, HTTP != 2xx) bleibt der Job in der Outbox und wird spaeter erneut versucht.

Backoff-Formel:
`delay = min(retry_cap_s, retry_base_s * 2^retry_count)`

Default:
1. `retry_base_s = 1.0`
2. `retry_cap_s = 60.0`

## 10.4 Watchdog-Gating
Der Raspi sendet Outbox-Nachrichten nur wenn Watchdog `up` ist.

Watchdog prueft:
1. Ping auf `peer_watchdog_host`
2. Optional HTTP Health auf `peer_base_url + peer_health_path`

Wenn Watchdog down:
1. Outbox wird nicht geloescht.
2. Versand pausiert bis Status wieder `up` ist.

## 11. Raspi Konfiguration (Microtom-relevant)
Datei: `/etc/mas004_rpi_databridge/config.json`

Wichtige Felder:
1. `peer_base_url`  
   Basis-URL eures Microtom-Servers, z. B. `https://192.168.1.64:9090`
2. `peer_watchdog_host`  
   Host fuer Ping
3. `peer_health_path`  
   meistens `/health`
4. `tls_verify`  
   `false` fuer self-signed Test, `true` mit gueltigen Zertifikaten
5. `shared_secret`  
   Aktiviert Secret-Pruefung fuer eingehendes Microtom -> Raspi
6. `http_timeout_s`, `retry_base_s`, `retry_cap_s`  
   bestimmen Robustheit und Retry-Verhalten

## 12. Konkreter Implementierungsplan fuer Microtom

## 12.1 Datenmodell (minimal)
Legt lokal eine Request-Tabelle an:

1. `request_id` (intern)
2. `out_idempotency_key` (an Raspi gesendet)
3. `command_line` (z. B. `TTP00002=?`)
4. `state` (`created`, `accepted`, `completed`, `failed`)
5. `result_line` (z. B. `TTP00002=16`)
6. `updated_ts`

Und eine Callback-Dedupe-Tabelle:

1. `callback_idempotency_key` (unique)
2. `correlation_id`
3. `received_ts`

## 12.2 Sender-Funktion
Ablauf:

1. Key erzeugen (z. B. UUID).
2. Request lokal mit `state=created` speichern.
3. `POST /api/inbox` senden mit diesem Key.
4. Bei `stored=true` -> `state=accepted`.
5. Bei Timeout/Netzwerkfehler Retry mit exakt demselben Key.

## 12.3 Callback-Handler
Ablauf:

1. Header `X-Idempotency-Key` lesen und deduplizieren.
2. Header `X-Correlation-Id` lesen.
3. Body `msg` lesen.
4. Passenden Request ueber Correlation-Key auf `completed` setzen.
5. HTTP 2xx zurueckgeben.

## 12.4 Beispielcode (didaktisch, nicht produktiv)
```python
from fastapi import FastAPI, Request
import requests
import uuid

RPI = "https://192.168.1.100:8080"
SHARED_SECRET = "SET_ME"

app = FastAPI()
open_requests = {}            # correlation -> command
seen_callbacks = set()        # callback idempotency keys

def send_command(line: str):
    key = str(uuid.uuid4())
    open_requests[key] = {"line": line, "state": "created"}
    headers = {
        "Content-Type": "application/json",
        "X-Idempotency-Key": key,
        "X-Shared-Secret": SHARED_SECRET,
    }
    body = {"cmd": line, "source": "microtom"}
    r = requests.post(f"{RPI}/api/inbox", headers=headers, json=body, timeout=5, verify=False)
    r.raise_for_status()
    j = r.json()
    open_requests[key]["state"] = "accepted" if j.get("stored") else "duplicate"
    return j

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/inbox")
async def callback(request: Request):
    cb_key = request.headers.get("x-idempotency-key", "")
    corr = request.headers.get("x-correlation-id", "")
    body = await request.json()
    if cb_key in seen_callbacks:
        return {"ok": True, "duplicate": True}
    seen_callbacks.add(cb_key)
    msg = str(body.get("msg", ""))
    if corr in open_requests:
        open_requests[corr]["state"] = "completed"
        open_requests[corr]["result"] = msg
    return {"ok": True}
```

## 13. Microtom-Simulator (Referenz-Testtool)
Datei:
`Raspberry-PLC/Microtom-Simulator/microtom_sim.py`

Simulator bietet:
1. `GET /health`
2. `POST /api/inbox` (empfaengt Raspi-Callback)
3. Konsolen-Input, der als Microtom-Request an Raspi geschickt wird

## 13.1 Startbeispiele
HTTP:
```bash
python microtom_sim.py --raspi https://<RPI-IP>:8080 --shared-secret "<SECRET>"
```

HTTPS:
```bash
python microtom_sim.py \
  --https \
  --certfile ./microtom.crt \
  --keyfile ./microtom.key \
  --raspi https://<RPI-IP>:8080 \
  --shared-secret "<SECRET>"
```

CLI Parameter:
1. `--host` (default `0.0.0.0`)
2. `--port` (default `9090`)
3. `--raspi` (Raspi Base URL)
4. `--verify` (TLS verify on)
5. `--https` + `--certfile` + `--keyfile`
6. `--shared-secret`

## 13.2 Typischer Testablauf
1. Simulator starten.
2. In Konsole eingeben: `TTP00002=?`
3. Erwartung:
   1. Sofort HTTP 200 mit `stored=true`.
   2. Kurz danach `RECV from Raspi -> MicrotomSim ...` mit Ergebnis.

## 14. Vollstaendige Endpoint-Uebersicht (Raspi)
Diese Liste hilft beim Lesen der API Docs.

## 14.1 Core fuer Microtom
1. `GET /health`
2. `POST /api/inbox`
3. `GET /api/inbox/next` (debug, token)
4. `POST /api/inbox/{msg_id}/ack` (debug, token)

## 14.2 Betriebs- und Konfig-Endpunkte (token)
1. `GET /api/ui/status`
2. `GET /api/config`
3. `POST /api/config`
4. `GET /api/system/network`
5. `POST /api/system/network`
6. `POST /api/outbox/enqueue`
7. `POST /api/test/send`

## 14.3 Parameter-Endpunkte (token)
1. `POST /api/params/import`
2. `GET /api/params/export`
3. `GET /api/params/list`
4. `POST /api/params/edit`

## 14.4 Log-Endpunkte (token)
1. `GET /api/ui/logs/channels`
2. `GET /api/ui/logs`
3. `POST /api/ui/logs/clear`
4. `GET /api/ui/logs/download`
5. `GET /api/logfiles/list`
6. `GET /api/logfiles/download`

## 14.5 UI/Docs Seiten
1. `GET /`
2. `GET /ui`
3. `GET /ui/params`
4. `GET /ui/settings`
5. `GET /ui/test`
6. `GET /docs`
7. `GET /docs/swagger`

## 15. Troubleshooting

## 15.1 `stored=false` bei Microtom -> Raspi
Ursache:
1. Gleiches `X-Idempotency-Key` bereits verwendet.

Loesung:
1. Fuer neuen Business-Request immer neuen Key erzeugen.
2. Nur bei Retry denselben Key wiederverwenden.

## 15.2 Kein Callback bei `stored=true`
Moegliche Ursachen:
1. Befehl konnte nicht geparst werden.
2. Body enthielt keines der Felder `msg/line/text/cmd`.
3. Raspi kann Microtom nicht erreichen (Netz/Routing/TLS).
4. Watchdog ist down und blockiert Outbox-Versand.

Checks am Raspi:
```bash
sudo journalctl -u mas004-rpi-databridge.service -f
sudo sqlite3 /var/lib/mas004_rpi_databridge/databridge.db \
  "select id,url,retry_count,datetime(next_attempt_ts,'unixepoch','localtime') from outbox order by id desc limit 20;"
```

## 15.3 `No route to host` im Raspi Journal
Ursache:
1. Raspi findet den Microtom-Host nicht im Routing.

Loesung:
1. `peer_base_url` und `peer_watchdog_host` pruefen.
2. Netzwerk und Subnetze auf beiden Seiten pruefen.

## 15.4 `401 Unauthorized (shared secret)`
Ursache:
1. Raspi erwartet Secret, Header fehlt oder ist falsch.

Loesung:
1. In Microtom `X-Shared-Secret` senden.
2. Wert gegen `/etc/mas004_rpi_databridge/config.json` pruefen.

## 16. Go-Live Checkliste
Vor produktivem Start:

1. Microtom `GET /health` liefert stabil 2xx.
2. Microtom `POST /api/inbox` verarbeitet Callback idempotent.
3. Microtom sendet Requests mit `X-Idempotency-Key`.
4. Correlation ueber `X-Correlation-Id` ist implementiert.
5. Shared Secret Strategie ist abgestimmt.
6. End-to-end Read und Write mit realen Befehlen getestet.
7. Netzwerkfehler-Test (Kabel ziehen / Host down) zeigt korrektes Retry-Verhalten.
8. Monitoring fuer Outbox-Backlog aktiv.
