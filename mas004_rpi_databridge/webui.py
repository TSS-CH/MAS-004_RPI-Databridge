from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Header, UploadFile, File, Query
from fastapi.responses import HTMLResponse, Response, PlainTextResponse
from pydantic import BaseModel
import subprocess
import os
import tempfile
import json

from mas004_rpi_databridge.config import Settings, DEFAULT_CFG_PATH
from mas004_rpi_databridge.db import DB
from mas004_rpi_databridge.outbox import Outbox
from mas004_rpi_databridge.inbox import Inbox
from mas004_rpi_databridge.params import ParamStore
from mas004_rpi_databridge.logstore import LogStore


def require_token(x_token: Optional[str], cfg: Settings):
    if cfg.ui_token and x_token != cfg.ui_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


class ConfigUpdate(BaseModel):
    peer_base_url: Optional[str] = None
    peer_watchdog_host: Optional[str] = None
    peer_health_path: Optional[str] = None
    tls_verify: Optional[bool] = None
    http_timeout_s: Optional[float] = None
    eth0_source_ip: Optional[str] = None
    webui_port: Optional[int] = None
    ui_token: Optional[str] = None


class OutboxEnqueue(BaseModel):
    method: str = "POST"
    path: str = "/api/inbox"
    url: Optional[str] = None
    headers: Dict[str, Any] = {}
    body: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None


class ParamEdit(BaseModel):
    pkey: str
    default_v: Optional[str] = None
    min_v: Optional[float] = None
    max_v: Optional[float] = None
    rw: Optional[str] = None


class UiSend(BaseModel):
    channel: str              # raspi | esp | laser | tto
    line: str                 # z.B. "TTP00002=?"
    correlation: Optional[str] = None


def build_app(cfg_path: str = DEFAULT_CFG_PATH) -> FastAPI:
    app = FastAPI(title="MAS-004_RPI-Databridge", version="0.3.0")

    cfg = Settings.load(cfg_path)
    db = DB(cfg.db_path)
    outbox = Outbox(db)
    inbox = Inbox(db)
    params = ParamStore(db)
    logs = LogStore(db)

    # -----------------------
    # Helpers
    # -----------------------
    def _enqueue_to_mikrotom(cfg2: Settings, line: str, correlation: Optional[str] = None, source: str = "raspi"):
        url = cfg2.peer_base_url.rstrip("/") + "/api/inbox"
        headers = {}
        if correlation:
            headers["X-Correlation-Id"] = correlation
        outbox.enqueue("POST", url, headers, {"msg": line, "source": source}, None)

    def _channel_norm(ch: str) -> str:
        ch = (ch or "").strip().lower()
        if ch in ("raspi", "esp", "laser", "tto"):
            return ch
        return "raspi"

    # -----------------------
    # Home / UI
    # -----------------------
    @app.get("/", response_class=HTMLResponse)
    def home():
        cfg2 = Settings.load(cfg_path)
        return f"""
        <html><body style="font-family:Arial;max-width:1100px;margin:20px">
        <h2>MAS-004_RPI-Databridge</h2>
        <p><b>eth0:</b> {cfg2.eth0_ip} | <b>eth1:</b> {cfg2.eth1_ip}</p>
        <p><b>Outbox:</b> {outbox.count()} | <b>Inbox pending:</b> {inbox.count_pending()}</p>
        <p><b>Peer:</b> {cfg2.peer_base_url} | Watchdog: {cfg2.peer_watchdog_host}</p>
        <p>
          <a href="/docs">API Docs</a> |
          <a href="/ui/params">Parameter UI</a> |
          <a href="/ui/test">Test UI</a>
        </p>
        </body></html>
        """

    @app.get("/ui", response_class=HTMLResponse)
    def ui():
        return home()

    @app.get("/health")
    def health():
        return {"ok": True}

    # -----------------------
    # Config
    # -----------------------
    @app.get("/api/config")
    def get_config():
        cfg2 = Settings.load(cfg_path)
        d = cfg2.__dict__.copy()
        d["ui_token"] = "***"
        return d

    @app.post("/api/config")
    def update_config(u: ConfigUpdate, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        for k, v in u.model_dump().items():
            if v is not None:
                setattr(cfg2, k, v)

        cfg2.save(cfg_path)
        subprocess.call(["bash", "-lc", "systemctl restart mas004-rpi-databridge.service"])
        return {"ok": True}

    # -----------------------
    # UI status / logs / send
    # -----------------------
    @app.get("/api/ui/status")
    def ui_status(x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        return {
            "ok": True,
            "outbox_count": outbox.count(),
            "inbox_pending": inbox.count_pending(),
            "peer_base_url": cfg2.peer_base_url,
        }

    @app.get("/api/ui/logs")
    def ui_logs(
        x_token: Optional[str] = Header(default=None),
        channel: str = Query(default="raspi"),
        limit: int = Query(default=200),
    ):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        ch = _channel_norm(channel)
        return {"ok": True, "channel": ch, "items": logs.list_logs(ch, limit=limit)}

    @app.get("/api/ui/logfile")
    def ui_logfile(
        x_token: Optional[str] = Header(default=None),
        channel: str = Query(default="raspi"),
    ):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        ch = _channel_norm(channel)
        txt = logs.read_logfile(ch)
        return PlainTextResponse(txt or "", media_type="text/plain; charset=utf-8")

    @app.post("/api/ui/send")
    def ui_send(req: UiSend, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        ch = _channel_norm(req.channel)
        line = (req.line or "").strip()
        if not line:
            raise HTTPException(status_code=400, detail="line fehlt")

        corr = req.correlation

        # Verhalten wie von dir beschrieben:
        # - Raspi-Fenster: direkt an Mikrotom senden
        # - ESP/LASER/TTO-Fenster: erst "an Raspi", dann Raspi -> Mikrotom (nur simuliert via Logs + enqueue)
        if ch == "raspi":
            logs.log("raspi", "out", f"manual->mikrotom: {line}")
            _enqueue_to_mikrotom(cfg2, line, correlation=corr, source="raspi")
            return {"ok": True, "sent": True, "mode": "raspi_to_mikrotom"}

        # device -> raspi
        logs.log(ch, "out", f"{ch}->raspi: {line}")
        logs.log("raspi", "in", f"{ch}: {line}")

        # raspi -> mikrotom
        logs.log("raspi", "out", f"to mikrotom: {line}")
        _enqueue_to_mikrotom(cfg2, line, correlation=corr, source=ch)
        return {"ok": True, "sent": True, "mode": f"{ch}_to_raspi_to_mikrotom"}

    # -----------------------
    # Outbox enqueue (admin)
    # -----------------------
    @app.post("/api/outbox/enqueue")
    def api_outbox_enqueue(req: OutboxEnqueue, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        url = req.url if req.url else cfg2.peer_base_url.rstrip("/") + req.path
        idem = outbox.enqueue(req.method, url, req.headers, req.body, req.idempotency_key)
        return {"ok": True, "idempotency_key": idem}

    # -----------------------
    # Inbox (peer -> raspi)
    # -----------------------
    @app.post("/api/inbox")
    async def api_inbox(
        request: Request,
        x_idempotency_key: Optional[str] = Header(default=None),
        x_shared_secret: Optional[str] = Header(default=None),
    ):
        body = None
        try:
            body = await request.json()
        except Exception:
            body = None

        headers = dict(request.headers)
        idem = x_idempotency_key or headers.get("x-idempotency-key") or "missing"
        source = request.client.host if request.client else None
        inserted = inbox.store(source, headers, body, idem)
        return {"ok": True, "stored": inserted, "idempotency_key": idem}

    @app.get("/api/inbox/next")
    def api_inbox_next(x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        msg = inbox.next_pending()
        if not msg:
            return {"ok": True, "msg": None}

        return {
            "ok": True,
            "msg": {
                "id": msg.id,
                "received_ts": msg.received_ts,
                "source": msg.source,
                "headers_json": msg.headers_json,
                "body_json": msg.body_json,
                "idempotency_key": msg.idempotency_key,
            },
        }

    @app.post("/api/inbox/{msg_id}/ack")
    def api_inbox_ack(msg_id: int, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        inbox.ack(msg_id)
        return {"ok": True}

    # -----------------------
    # Params API
    # -----------------------
    @app.post("/api/params/import")
    async def params_import(file: UploadFile = File(...), x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        suffix = os.path.splitext(file.filename or "")[1].lower()
        if suffix not in (".xlsx",):
            raise HTTPException(status_code=400, detail="Bitte eine .xlsx Datei hochladen")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp_path = tmp.name
            content = await file.read()
            tmp.write(content)

        try:
            res = params.import_xlsx(tmp_path)
            return res
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    @app.get("/api/params/export")
    def params_export(
        x_token: Optional[str] = Header(default=None),
        ptype: Optional[str] = Query(default=None),
        q: Optional[str] = Query(default=None),
    ):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        data = params.export_xlsx_bytes(ptype=ptype, q=q)  # muss in ParamStore existieren
        filename = "params_export.xlsx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/params/list")
    def params_list(
        x_token: Optional[str] = Header(default=None),
        ptype: Optional[str] = Query(default=None),
        q: Optional[str] = Query(default=None),
        limit: int = Query(default=200),
        offset: int = Query(default=0),
    ):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        return {"ok": True, "items": params.list_params(ptype=ptype, q=q, limit=limit, offset=offset)}

    @app.post("/api/params/edit")
    def params_edit(req: ParamEdit, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        ok, msg = params.update_meta(  # muss in ParamStore existieren
            pkey=req.pkey,
            default_v=req.default_v,
            min_v=req.min_v,
            max_v=req.max_v,
            rw=req.rw,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return {"ok": True, "msg": msg}

    # -----------------------
    # Simple Parameter UI
    # -----------------------
    @app.get("/ui/params", response_class=HTMLResponse)
    def ui_params():
        # (Dein HTML/JS von vorher – unverändert)
        return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Params UI</title>
  <style>
    body{font-family:Arial; margin:20px; max-width:1200px}
    table{border-collapse:collapse; width:100%}
    th,td{border:1px solid #ddd; padding:6px; font-size:13px}
    th{background:#f3f3f3; position:sticky; top:0}
    input{padding:6px; margin:4px}
    .row{display:flex; gap:10px; align-items:center; flex-wrap:wrap}
    .btn{padding:7px 10px; cursor:pointer}
    .muted{color:#666}
    .pill{padding:2px 6px; border:1px solid #aaa; border-radius:10px; font-size:12px}
  </style>
</head>
<body>
  <h2>Parameter UI</h2>
  <p class="muted">Token wird im Browser gespeichert (localStorage). Export/Import/Edit braucht X-Token.</p>

  <div class="row">
    <label>UI Token:</label>
    <input id="token" style="width:420px" placeholder="MAS004-..."/>
    <button class="btn" onclick="saveToken()">Save</button>
    <span id="tokstate" class="pill"></span>
  </div>

  <hr/>

  <div class="row">
    <input id="q" style="width:280px" placeholder="Suche (pkey/name/message)"/>
    <input id="ptype" style="width:120px" placeholder="ptype (z.B. TTP)"/>
    <button class="btn" onclick="load()">Reload</button>

    <button class="btn" onclick="exportXlsx()">Export XLSX</button>

    <input type="file" id="file" accept=".xlsx"/>
    <button class="btn" onclick="importXlsx()">Import XLSX</button>

    <span id="status" class="muted"></span>
  </div>

  <h3>Liste</h3>
  <table>
    <thead>
      <tr>
        <th>pkey</th><th>min</th><th>max</th><th>default</th><th>rw</th>
        <th>current</th><th>effective</th><th>name</th><th>message</th><th>edit</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>

<script>
function getToken(){ return localStorage.getItem("mas004_ui_token") || ""; }
function saveToken(){
  localStorage.setItem("mas004_ui_token", document.getElementById("token").value.trim());
  showTok();
}
function showTok(){
  const t = getToken();
  document.getElementById("token").value = t;
  document.getElementById("tokstate").textContent = t ? "token ok" : "no token";
}
async function api(path, opt={}){
  opt.headers = opt.headers || {};
  const t = getToken();
  if(t) opt.headers["X-Token"] = t;
  const r = await fetch(path, opt);
  const txt = await r.text();
  let j=null; try{ j=JSON.parse(txt); }catch(e){}
  if(!r.ok){
    throw new Error((j && j.detail) ? j.detail : ("HTTP "+r.status+" "+txt));
  }
  return j;
}

async function load(){
  const q = document.getElementById("q").value.trim();
  const ptype = document.getElementById("ptype").value.trim();
  document.getElementById("status").textContent = "loading...";
  const url = `/api/params/list?limit=400&offset=0` + (q?`&q=${encodeURIComponent(q)}`:"") + (ptype?`&ptype=${encodeURIComponent(ptype)}`:"");
  const j = await api(url);
  const tb = document.getElementById("tbody");
  tb.innerHTML = "";
  for(const it of j.items){
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${it.pkey}</td>
      <td>${it.min_v ?? ""}</td>
      <td>${it.max_v ?? ""}</td>
      <td>${it.default_v ?? ""}</td>
      <td>${it.rw ?? ""}</td>
      <td>${it.current_v ?? ""}</td>
      <td>${it.effective_v ?? ""}</td>
      <td>${it.name ?? ""}</td>
      <td>${it.message ?? ""}</td>
      <td><button class="btn" onclick="edit('${it.pkey}','${it.min_v ?? ""}','${it.max_v ?? ""}','${it.default_v ?? ""}','${it.rw ?? ""}')">edit</button></td>
    `;
    tb.appendChild(tr);
  }
  document.getElementById("status").textContent = `ok: ${j.items.length} items`;
}

async function edit(pkey, minv, maxv, defv, rw){
  const nmin = prompt(`min_v für ${pkey}`, minv);
  if(nmin === null) return;
  const nmax = prompt(`max_v für ${pkey}`, maxv);
  if(nmax === null) return;
  const ndef = prompt(`default_v für ${pkey}`, defv);
  if(ndef === null) return;
  const nrw = prompt(`rw für ${pkey} (R / W / R/W)`, rw);
  if(nrw === null) return;

  const payload = {
    pkey: pkey,
    min_v: (nmin.trim()===""? null : Number(nmin)),
    max_v: (nmax.trim()===""? null : Number(nmax)),
    default_v: (ndef.trim()===""? null : ndef),
    rw: (nrw.trim()===""? null : nrw)
  };

  document.getElementById("status").textContent = "saving...";
  await api("/api/params/edit", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });
  await load();
}

async function importXlsx(){
  const f = document.getElementById("file").files[0];
  if(!f){ alert("Bitte .xlsx auswählen"); return; }
  document.getElementById("status").textContent = "importing...";
  const fd = new FormData();
  fd.append("file", f);
  const t = getToken();
  const r = await fetch("/api/params/import", {method:"POST", body: fd, headers: t?{"X-Token":t}:{}} );
  const txt = await r.text();
  if(!r.ok){ alert("Import Fehler: " + txt); return; }
  document.getElementById("status").textContent = "import ok";
  await load();
}

function exportXlsx(){
  const q = document.getElementById("q").value.trim();
  const ptype = document.getElementById("ptype").value.trim();
  let url = "/api/params/export" + (q||ptype ? "?" : "");
  if(q) url += "q=" + encodeURIComponent(q) + "&";
  if(ptype) url += "ptype=" + encodeURIComponent(ptype) + "&";
  url = url.replace(/[&?]$/, "");

  (async ()=>{
    const t = getToken();
    const r = await fetch(url, {headers: t?{"X-Token":t}:{}} );
    if(!r.ok){ alert("Export Fehler: " + await r.text()); return; }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "params_export.xlsx";
    a.click();
    URL.revokeObjectURL(a.href);
  })();
}

showTok();
load();
</script>
</body>
</html>
        """

    # -----------------------
    # Test UI (Tabs + Logs + Send)
    # -----------------------
    @app.get("/ui/test", response_class=HTMLResponse)
    def ui_test():
        return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>MAS-004 Test UI</title>
  <style>
    body{font-family:Arial; margin:20px; max-width:1200px}
    .row{display:flex; gap:10px; align-items:center; flex-wrap:wrap}
    .tabs{display:flex; gap:8px; margin-bottom:10px}
    .tab{padding:8px 12px; border:1px solid #aaa; border-radius:10px; cursor:pointer}
    .tab.active{background:#f0f0f0; font-weight:bold}
    textarea{width:100%; height:70px; font-family:Consolas, monospace; font-size:13px}
    pre{white-space:pre-wrap; background:#111; color:#eee; padding:10px; border-radius:10px; height:420px; overflow:auto}
    input{padding:6px}
    button{padding:7px 10px; cursor:pointer}
    .muted{color:#666}
    .pill{padding:2px 6px; border:1px solid #aaa; border-radius:10px; font-size:12px}
    .right{margin-left:auto}
  </style>
</head>
<body>
  <h2>MAS-004 Test UI</h2>
  <p class="muted">Tabs simulieren die jeweiligen Komponenten. Send aus ESP/LASER/TTO läuft über Raspi -> Mikrotom (nur Simulation via Logs + Outbox).</p>

  <div class="row">
    <label>UI Token:</label>
    <input id="token" style="width:420px" placeholder="MAS004-..."/>
    <button onclick="saveToken()">Save</button>
    <span id="tokstate" class="pill"></span>

    <span class="right muted" id="status"></span>
  </div>

  <hr/>

  <div class="tabs">
    <div class="tab active" data-ch="raspi" onclick="setTab(this)">RASPI</div>
    <div class="tab" data-ch="esp" onclick="setTab(this)">ESP-PLC</div>
    <div class="tab" data-ch="laser" onclick="setTab(this)">VJ3350</div>
    <div class="tab" data-ch="tto" onclick="setTab(this)">VJ6530</div>
  </div>

  <div class="row">
    <input id="line" style="flex:1" placeholder="z.B. TTP00002=?  oder  TTE1000=1  oder  MAS0026=20" />
    <button onclick="sendLine()">Send</button>
    <button onclick="reloadLogs()">Reload Logs</button>
    <button onclick="showLogfile()">Logfile</button>
    <label class="muted"><input type="checkbox" id="autoref" checked/> Auto</label>
  </div>

  <pre id="log"></pre>

<script>
let cur = "raspi";
let timer = null;

function getToken(){ return localStorage.getItem("mas004_ui_token") || ""; }
function saveToken(){
  localStorage.setItem("mas004_ui_token", document.getElementById("token").value.trim());
  showTok();
}
function showTok(){
  const t = getToken();
  document.getElementById("token").value = t;
  document.getElementById("tokstate").textContent = t ? "token ok" : "no token";
}
async function api(path, opt={}){
  opt.headers = opt.headers || {};
  const t = getToken();
  if(t) opt.headers["X-Token"] = t;
  const r = await fetch(path, opt);
  const txt = await r.text();
  let j=null; try{ j=JSON.parse(txt); }catch(e){}
  if(!r.ok){
    throw new Error((j && j.detail) ? j.detail : ("HTTP "+r.status+" "+txt));
  }
  return j;
}

function setTab(el){
  document.querySelectorAll(".tab").forEach(t=>t.classList.remove("active"));
  el.classList.add("active");
  cur = el.getAttribute("data-ch");
  reloadLogs();
}

async function reloadStatus(){
  try{
    const j = await api("/api/ui/status");
    document.getElementById("status").textContent = `outbox=${j.outbox_count} | inbox_pending=${j.inbox_pending} | peer=${j.peer_base_url}`;
  }catch(e){
    document.getElementById("status").textContent = "status: " + e.message;
  }
}

async function reloadLogs(){
  try{
    const j = await api(`/api/ui/logs?channel=${encodeURIComponent(cur)}&limit=250`);
    const lines = j.items.map(it=>{
      const d = (it.direction||"").toUpperCase().padEnd(5," ");
      const ts = new Date((it.ts||0)*1000).toISOString();
      return `[${ts}] ${d} ${it.message||""}`;
    });
    document.getElementById("log").textContent = lines.join("\\n");
  }catch(e){
    document.getElementById("log").textContent = "LOG ERROR: " + e.message;
  }
}

async function sendLine(){
  const line = document.getElementById("line").value.trim();
  if(!line){ alert("Bitte line eingeben"); return; }
  try{
    await api("/api/ui/send", {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({channel: cur, line: line})
    });
    document.getElementById("line").value = "";
    await reloadStatus();
    await reloadLogs();
  }catch(e){
    alert("Send Fehler: " + e.message);
  }
}

async function showLogfile(){
  try{
    const t = getToken();
    const r = await fetch(`/api/ui/logfile?channel=${encodeURIComponent(cur)}`, {headers: t?{"X-Token":t}:{}} );
    const txt = await r.text();
    const w = window.open("", "_blank");
    w.document.write("<pre style='white-space:pre-wrap;font-family:Consolas,monospace'>" + (txt.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")) + "</pre>");
  }catch(e){
    alert("Logfile Fehler: " + e.message);
  }
}

function startAuto(){
  if(timer) clearInterval(timer);
  timer = setInterval(async ()=>{
    if(!document.getElementById("autoref").checked) return;
    await reloadStatus();
    await reloadLogs();
  }, 1000);
}

showTok();
reloadStatus();
reloadLogs();
startAuto();
</script>
</body>
</html>
        """

    return app
