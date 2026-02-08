# MAS-004 Microtom Interface QuickStart

Version: 1.0  
Zielgruppe: Microtom-Entwicklerteam  
Referenz: `docs/Microtom_Interface.md` (vollstaendige Spezifikation)

## 1. Ziel in 10 Minuten
Nach diesem QuickStart kann Microtom:

1. Kommandos an Raspi senden (`POST /api/inbox`).
2. Asynchrone Antworten vom Raspi empfangen (`POST /api/inbox` auf Microtom-Seite).
3. Health fuer Raspi-Watchdog bereitstellen (`GET /health`).
4. Idempotent und robust mit Retries arbeiten.

## 2. Minimaler Schnittstellenvertrag

## 2.1 Microtom -> Raspi
Endpoint:

`POST https://<RPI-IP>:8080/api/inbox`

Pflicht-Header:

1. `X-Idempotency-Key: <unique-per-request>`
2. `Content-Type: application/json`

Optional:

1. `X-Shared-Secret: <secret>` (wenn am Raspi aktiviert)

Body (empfohlen):
```json
{"cmd":"TTP00002=16"}
```

Sofort-Response vom Raspi:
```json
{
  "ok": true,
  "stored": true,
  "idempotency_key": "microtom-20260208-0001"
}
```

Wichtig:

1. Das ist nur die Empfangsbestaetigung, noch nicht das fachliche Ergebnis.
2. Fachliches Ergebnis kommt asynchron per Callback von Raspi zu Microtom.

## 2.2 Raspi -> Microtom (Callback)
Microtom muss bereitstellen:

1. `GET /health` -> `2xx`
2. `POST /api/inbox` -> nimmt Raspi-Resultate an

Callback-Body (vom Raspi):
```json
{"msg":"ACK_TTP00002=16","source":"raspi"}
```

Relevante Header:

1. `X-Idempotency-Key` (Raspi-seitiger Outbound-Key)
2. `X-Correlation-Id` (urspruenglicher Microtom-Key)

## 3. Nachrichtenformat

## 3.1 Syntax
```text
<PTYPE><PID>=<VALUE>
```

Read:
```text
<PTYPE><PID>=?
```

Beispiele:

1. `TTP00002=?`
2. `TTP00002=16`
3. `MAS0026=20`
4. `LSW1000=1`

## 3.2 Erfolgs-/Fehlerantworten
Erfolg:

1. Read: `TTP00002=16`
2. Write: `ACK_TTP00002=16`

Fehler:

1. `TTP00002=NAK_ReadOnly`
2. `TTP00002=NAK_UnknownParam`
3. `TTP00002=NAK_DeviceDown`
4. `TTP00002=NAK_OutOfRange`
5. `TTP00002=NAK_DeviceComm`

## 4. Minimal-Implementierung (Python/FastAPI Beispiel)
Der Code zeigt nur das Minimum fuer Integration.

```python
from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/inbox")
async def inbox(request: Request):
    body = await request.json()
    correlation = request.headers.get("X-Correlation-Id", "")
    idem = request.headers.get("X-Idempotency-Key", "")
    # TODO: dedupe via idem
    # TODO: correlate response to outgoing request via correlation
    # body["msg"] contains the business result
    return {"ok": True}
```

## 5. Idempotenz und Korrelation (Pflicht fuer Stabilitaet)

## 5.1 Outgoing request (Microtom -> Raspi)
1. Pro Business-Request eindeutigen Key erzeugen.
2. Key in `X-Idempotency-Key` senden.
3. Bei eigenem Retry denselben Key wiederverwenden.

## 5.2 Incoming callback (Raspi -> Microtom)
1. Callback via `X-Idempotency-Key` deduplizieren.
2. `X-Correlation-Id` als Join-Key auf ursprünglichen Request verwenden.

Empfohlene Request-Tabelle:

1. `request_id`
2. `idempotency_key_out`
3. `state` (`sent`, `accepted`, `completed`, `failed`)
4. `last_callback_msg`
5. `updated_ts`

## 6. Schnelltest mit curl

## 6.1 Read senden
```bash
curl -k -X POST "https://<RPI-IP>:8080/api/inbox" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: qs-read-0001" \
  -H "X-Shared-Secret: <SECRET>" \
  -d "{\"cmd\":\"TTP00002=?\"}"
```

## 6.2 Write senden
```bash
curl -k -X POST "https://<RPI-IP>:8080/api/inbox" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: qs-write-0001" \
  -H "X-Shared-Secret: <SECRET>" \
  -d "{\"cmd\":\"TTP00002=16\"}"
```

Erwartung:

1. Sofort `stored=true`.
2. Kurz danach Callback auf Microtom `/api/inbox` mit `msg`.

## 7. Schnelltest mit Microtom-Simulator
Simulator-Datei:

`Raspberry-PLC/Microtom-Simulator/microtom_sim.py`

Start:
```bash
python microtom_sim.py \
  --https \
  --certfile ./microtom.crt \
  --keyfile ./microtom.key \
  --raspi https://<RPI-IP>:8080 \
  --shared-secret "<SECRET>"
```

Dann in der Konsole eingeben:

1. `TTP00002=?`
2. `TTP00002=14`
3. `MAS0026=20`

## 8. Betrieb und Monitoring

Raspi Service-Logs:
```bash
sudo journalctl -u mas004-rpi-databridge.service -f
```

Outbox-Status:
```bash
sudo sqlite3 /var/lib/mas004_rpi_databridge/databridge.db \
  "select id,url,retry_count,datetime(next_attempt_ts,'unixepoch','localtime') from outbox order by id desc limit 20;"
```

Interpretation:

1. `retry_count` steigt -> Microtom-Callback-Endpunkt nicht stabil erreichbar.
2. `No route to host` -> Netzwerkproblem/Routing.
3. `stored=false` beim Senden -> Idempotency-Key wurde wiederverwendet.

## 9. Go-Live Checkliste
Vor produktivem Start:

1. Microtom `/health` liefert stabil `2xx`.
2. Microtom `/api/inbox` ist idempotent und schnell (`2xx` bei Erfolg).
3. Shared-Secret auf beiden Seiten identisch konfiguriert.
4. TLS-Strategie definiert (`tls_verify=true` bei gueltigem Zertifikat).
5. End-to-End Read/Write inkl. Callback und Dedupe getestet.
6. Monitoring fuer Retries/Backlog aktiv.

## 10. Wenn etwas unklar ist
Fuer Detailregeln (alle NAK-Faelle, Parserdetails, Retry/Watchdog intern):

1. `docs/Microtom_Interface.md`  
2. `mas004_rpi_databridge/webui.py` (API-Endpunkte)  
3. `mas004_rpi_databridge/router.py` (Routing und Callback-Flow)

