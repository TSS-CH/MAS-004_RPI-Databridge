from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Header, UploadFile, File, Query
from fastapi.responses import HTMLResponse, Response, PlainTextResponse
from pydantic import BaseModel
import subprocess
import os
import tempfile

from mas004_rpi_databridge.config import Settings, DEFAULT_CFG_PATH
from mas004_rpi_databridge.db import DB
from mas004_rpi_databridge.outbox import Outbox
from mas004_rpi_databridge.inbox import Inbox
from mas004_rpi_databridge.params import ParamStore
from mas004_rpi_databridge.logstore import LogStore
from mas004_rpi_databridge.netconfig import IfaceCfg, apply_static, get_current_ip_info


def require_token(x_token: Optional[str], cfg: Settings):
    if cfg.ui_token and x_token != cfg.ui_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


class ConfigUpdate(BaseModel):
    # Mikrotom
    peer_base_url: Optional[str] = None
    peer_watchdog_host: Optional[str] = None
    peer_health_path: Optional[str] = None

    # HTTP/tls
    tls_verify: Optional[bool] = None
    http_timeout_s: Optional[float] = None
    eth0_source_ip: Optional[str] = None

    # webui
    webui_port: Optional[int] = None
    ui_token: Optional[str] = None
    shared_secret: Optional[str] = None

    # device endpoints
    esp_host: Optional[str] = None
    esp_port: Optional[int] = None
    vj3350_host: Optional[str] = None
    vj3350_port: Optional[int] = None
    vj6530_host: Optional[str] = None
    vj6530_port: Optional[int] = None


class NetworkUpdate(BaseModel):
    eth0_ip: str
    eth0_prefix: int
    eth0_gateway: str
    eth1_ip: str
    eth1_prefix: int
    eth1_gateway: str
    apply_now: bool = False  # wenn true -> versucht Netzwerk live umzustellen


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


def build_app(cfg_path: str = DEFAULT_CFG_PATH) -> FastAPI:
    app = FastAPI(title="MAS-004_RPI-Databridge", version="0.3.0")

    cfg = Settings.load(cfg_path)
    db = DB(cfg.db_path)
    outbox = Outbox(db)
    inbox = Inbox(db)
    params = ParamStore(db)
    logs = LogStore(db)

    # -----------------------------
    # Home
    # -----------------------------
    @app.get("/", response_class=HTMLResponse)
    def home():
        cfg2 = Settings.load(cfg_path)
        return f"""
        <html><body style="font-family:Arial;max-width:1000px;margin:20px">
        <h2>MAS-004_RPI-Databridge</h2>
        <p><b>eth0:</b> {cfg2.eth0_ip} | <b>eth1:</b> {cfg2.eth1_ip}</p>
        <p><b>Outbox:</b> {outbox.count()} | <b>Inbox pending:</b> {inbox.count_pending()}</p>
        <p><b>Peer:</b> {cfg2.peer_base_url} | Watchdog: {cfg2.peer_watchdog_host}</p>
        <p>
          <a href="/docs">API Docs</a> |
          <a href="/ui/params">Parameter UI</a> |
          <a href="/ui/test">Test UI</a> |
          <a href="/ui/settings">System Settings</a>
        </p>
        </body></html>
        """

    @app.get("/ui", response_class=HTMLResponse)
    def ui():
        return home()

    @app.get("/health")
    def health():
        return {"ok": True}

    # -----------------------------
    # UI status
    # -----------------------------
    @app.get("/api/ui/status")
    def ui_status(x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        return {
            "ok": True,
            "outbox_count": outbox.count(),
            "inbox_pending": inbox.count_pending(),
            "peer_base_url": cfg2.peer_base_url,
            "devices": {
                "esp": {"host": cfg2.esp_host, "port": cfg2.esp_port},
                "vj3350": {"host": cfg2.vj3350_host, "port": cfg2.vj3350_port},
                "vj6530": {"host": cfg2.vj6530_host, "port": cfg2.vj6530_port},
            }
        }

    # -----------------------------
    # Config API (Databridge + device endpoints)
    # -----------------------------
    @app.get("/api/config")
    def get_config(x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        d = cfg2.__dict__.copy()
        d["ui_token"] = "***"
        d["shared_secret"] = "***" if (cfg2.shared_secret or "") else ""
        return {"ok": True, "config": d}

    @app.post("/api/config")
    def update_config(u: ConfigUpdate, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        for k, v in u.model_dump().items():
            if v is not None:
                setattr(cfg2, k, v)

        cfg2.save(cfg_path)
        # Restart service to apply
        subprocess.call(["bash", "-lc", "systemctl restart mas004-rpi-databridge.service"])
        return {"ok": True}

    # -----------------------------
    # Network API (eth0/eth1)
    # -----------------------------
    @app.get("/api/system/network")
    def get_network(x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        return {"ok": True, "config": {
            "eth0_ip": cfg2.eth0_ip, "eth0_subnet": cfg2.eth0_subnet, "eth0_gateway": cfg2.eth0_gateway,
            "eth1_ip": cfg2.eth1_ip, "eth1_subnet": cfg2.eth1_subnet, "eth1_gateway": cfg2.eth1_gateway,
        }, "status": get_current_ip_info()}

    @app.post("/api/system/network")
    def set_network(req: NetworkUpdate, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        # Save into config.json
        cfg2.eth0_ip = req.eth0_ip
        cfg2.eth0_subnet = str(req.eth0_prefix)
        cfg2.eth0_gateway = req.eth0_gateway

        cfg2.eth1_ip = req.eth1_ip
        cfg2.eth1_subnet = str(req.eth1_prefix)
        cfg2.eth1_gateway = req.eth1_gateway

        cfg2.save(cfg_path)

        applied = []
        if req.apply_now:
            # try to apply immediately
            r0 = apply_static("eth0", IfaceCfg(ip=req.eth0_ip, prefix=req.eth0_prefix, gw=req.eth0_gateway))
            r1 = apply_static("eth1", IfaceCfg(ip=req.eth1_ip, prefix=req.eth1_prefix, gw=req.eth1_gateway))
            applied = [("eth0", r0), ("eth1", r1)]

        return {"ok": True, "applied": applied}

    # -----------------------------
    # Outbox enqueue helper
    # -----------------------------
    @app.post("/api/outbox/enqueue")
    def api_outbox_enqueue(req: OutboxEnqueue, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        url = req.url if req.url else cfg2.peer_base_url.rstrip("/") + req.path
        idem = outbox.enqueue(req.method, url, req.headers, req.body, req.idempotency_key)
        return {"ok": True, "idempotency_key": idem}

    # -----------------------------
    # Inbox (receive from Mikrotom)
    # -----------------------------
    @app.post("/api/inbox")
    async def api_inbox(
        request: Request,
        x_idempotency_key: Optional[str] = Header(default=None),
        x_shared_secret: Optional[str] = Header(default=None),
    ):
        cfg2 = Settings.load(cfg_path)
        # optional shared secret check (if set)
        if (cfg2.shared_secret or "") and x_shared_secret != cfg2.shared_secret:
            raise HTTPException(status_code=401, detail="Unauthorized (shared secret)")

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

    # =========================
    # ===== PARAMS API ========
    # =========================
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

        data = params.export_xlsx_bytes(ptype=ptype, q=q)
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
        ok, msg = params.update_meta(
            pkey=req.pkey,
            default_v=req.default_v,
            min_v=req.min_v,
            max_v=req.max_v,
            rw=req.rw,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return {"ok": True, "msg": msg}

    # =========================
    # ===== LOG API ===========
    # =========================
    @app.get("/api/ui/logs/channels")
    def log_channels(x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        return {"ok": True, "channels": logs.list_channels()}

    @app.get("/api/ui/logs")
    def get_logs(
        x_token: Optional[str] = Header(default=None),
        channel: str = Query(...),
        limit: int = Query(default=250),
    ):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        return {"ok": True, "items": logs.list_logs(channel, limit=limit)}

    @app.post("/api/ui/logs/clear")
    def clear_logs(
        x_token: Optional[str] = Header(default=None),
        channel: str = Query(...),
    ):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        return logs.clear_channel(channel)

    @app.get("/api/ui/logs/download")
    def download_log(
        x_token: Optional[str] = Header(default=None),
        channel: str = Query(...),
    ):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)
        data = logs.read_logfile(channel)
        return Response(
            content=data.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{channel}.log"'},
        )

    # =========================
    # ===== SIMPLE UI =========
    # =========================
    @app.get("/ui/params", response_class=HTMLResponse)
    def ui_params():
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

    # -----------------------------
    # Settings UI
    # -----------------------------
    @app.get("/ui/settings", response_class=HTMLResponse)
    def ui_settings():
        return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>System Settings</title>
  <style>
    body{font-family:Arial; margin:20px; max-width:1100px}
    .row{display:flex; gap:10px; align-items:center; flex-wrap:wrap}
    input{padding:6px; margin:4px}
    button{padding:7px 10px; cursor:pointer}
    fieldset{border:1px solid #ddd; margin:12px 0; padding:12px; border-radius:8px}
    legend{padding:0 6px; color:#333}
    pre{background:#111; color:#eee; padding:10px; border-radius:10px; overflow:auto; max-height:260px}
    .muted{color:#666}
    .pill{padding:2px 6px; border:1px solid #aaa; border-radius:10px; font-size:12px}
  </style>
</head>
<body>
  <h2>System Settings</h2>
  <p class="muted">Token wird im Browser gespeichert (localStorage). Änderungen an Network können dich aussperren – daher „Apply now“ bewusst setzen.</p>

  <div class="row">
    <label>UI Token:</label>
    <input id="token" style="width:420px" placeholder="MAS004-..."/>
    <button onclick="saveToken()">Save</button>
    <span id="tokstate" class="pill"></span>
    <a href="/" style="margin-left:auto">Home</a>
  </div>

  <fieldset>
    <legend>Raspi Network (eth0/eth1)</legend>
    <div class="row">
      <label>eth0 IP</label><input id="eth0_ip" style="width:160px"/>
      <label>Prefix</label><input id="eth0_pre" style="width:60px" placeholder="24"/>
      <label>GW</label><input id="eth0_gw" style="width:160px"/>
    </div>
    <div class="row">
      <label>eth1 IP</label><input id="eth1_ip" style="width:160px"/>
      <label>Prefix</label><input id="eth1_pre" style="width:60px" placeholder="24"/>
      <label>GW</label><input id="eth1_gw" style="width:160px"/>
    </div>
    <div class="row">
      <label><input type="checkbox" id="apply_now"/> Apply now (live setzen)</label>
      <button onclick="saveNetwork()">Save Network</button>
      <button onclick="reloadAll()">Reload</button>
      <span id="net_status" class="muted"></span>
    </div>
    <h4>Status</h4>
    <pre id="netinfo"></pre>
  </fieldset>

  <fieldset>
    <legend>Databridge / Mikrotom</legend>
    <div class="row">
      <label>peer_base_url</label><input id="peer_base_url" style="width:420px"/>
      <label>peer_watchdog_host</label><input id="peer_watchdog_host" style="width:160px"/>
      <label>peer_health_path</label><input id="peer_health_path" style="width:120px"/>
    </div>
    <div class="row">
      <label>http_timeout_s</label><input id="http_timeout_s" style="width:80px"/>
      <label>tls_verify</label><input id="tls_verify" style="width:80px" placeholder="true/false"/>
      <label>eth0_source_ip</label><input id="eth0_source_ip" style="width:160px"/>
    </div>
    <div class="row">
      <label>shared_secret</label><input id="shared_secret" style="width:420px" placeholder="(leer = aus)"/>
    </div>
    <div class="row">
      <button onclick="saveBridge()">Save Bridge + Restart</button>
      <span id="bridge_status" class="muted"></span>
    </div>
  </fieldset>

  <fieldset>
    <legend>Device Endpoints (ESP / VJ3350 / VJ6530)</legend>
    <div class="row">
      <label>ESP host</label><input id="esp_host" style="width:160px"/>
      <label>ESP port</label><input id="esp_port" style="width:80px"/>
    </div>
    <div class="row">
      <label>VJ3350 host</label><input id="vj3350_host" style="width:160px"/>
      <label>VJ3350 port</label><input id="vj3350_port" style="width:80px"/>
    </div>
    <div class="row">
      <label>VJ6530 host</label><input id="vj6530_host" style="width:160px"/>
      <label>VJ6530 port</label><input id="vj6530_port" style="width:80px"/>
    </div>
    <div class="row">
      <button onclick="saveDevices()">Save Devices + Restart</button>
      <span id="dev_status" class="muted"></span>
    </div>
  </fieldset>

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

async function reloadAll(){
  showTok();
  // config
  const cfg = await api("/api/config");
  const c = cfg.config;
  document.getElementById("peer_base_url").value = c.peer_base_url || "";
  document.getElementById("peer_watchdog_host").value = c.peer_watchdog_host || "";
  document.getElementById("peer_health_path").value = c.peer_health_path || "";
  document.getElementById("http_timeout_s").value = c.http_timeout_s ?? "";
  document.getElementById("tls_verify").value = String(c.tls_verify ?? false);
  document.getElementById("eth0_source_ip").value = c.eth0_source_ip || "";
  document.getElementById("shared_secret").value = (c.shared_secret && c.shared_secret!=="***") ? c.shared_secret : "";

  document.getElementById("esp_host").value = c.esp_host || "";
  document.getElementById("esp_port").value = c.esp_port ?? "";
  document.getElementById("vj3350_host").value = c.vj3350_host || "";
  document.getElementById("vj3350_port").value = c.vj3350_port ?? "";
  document.getElementById("vj6530_host").value = c.vj6530_host || "";
  document.getElementById("vj6530_port").value = c.vj6530_port ?? "";

  // network
  const net = await api("/api/system/network");
  const n = net.config;
  document.getElementById("eth0_ip").value = n.eth0_ip || "";
  document.getElementById("eth0_pre").value = n.eth0_subnet || "";
  document.getElementById("eth0_gw").value = n.eth0_gateway || "";
  document.getElementById("eth1_ip").value = n.eth1_ip || "";
  document.getElementById("eth1_pre").value = n.eth1_subnet || "";
  document.getElementById("eth1_gw").value = n.eth1_gateway || "";
  document.getElementById("netinfo").textContent = JSON.stringify(net.status, null, 2);
}

async function saveNetwork(){
  document.getElementById("net_status").textContent = "saving...";
  const payload = {
    eth0_ip: document.getElementById("eth0_ip").value.trim(),
    eth0_prefix: Number(document.getElementById("eth0_pre").value.trim()),
    eth0_gateway: document.getElementById("eth0_gw").value.trim(),
    eth1_ip: document.getElementById("eth1_ip").value.trim(),
    eth1_prefix: Number(document.getElementById("eth1_pre").value.trim()),
    eth1_gateway: document.getElementById("eth1_gw").value.trim(),
    apply_now: document.getElementById("apply_now").checked
  };
  const j = await api("/api/system/network", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
  document.getElementById("net_status").textContent = "ok";
  if(j.applied && j.applied.length){
    alert("Applied:\\n" + JSON.stringify(j.applied, null, 2));
  }
  await reloadAll();
}

async function saveBridge(){
  document.getElementById("bridge_status").textContent = "saving...";
  const payload = {
    peer_base_url: document.getElementById("peer_base_url").value.trim(),
    peer_watchdog_host: document.getElementById("peer_watchdog_host").value.trim(),
    peer_health_path: document.getElementById("peer_health_path").value.trim(),
    http_timeout_s: Number(document.getElementById("http_timeout_s").value.trim()),
    tls_verify: (document.getElementById("tls_verify").value.trim().toLowerCase()==="true"),
    eth0_source_ip: document.getElementById("eth0_source_ip").value.trim(),
    shared_secret: document.getElementById("shared_secret").value.trim()
  };
  await api("/api/config", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
  document.getElementById("bridge_status").textContent = "saved (service restarted)";
}

async function saveDevices(){
  document.getElementById("dev_status").textContent = "saving...";
  const payload = {
    esp_host: document.getElementById("esp_host").value.trim(),
    esp_port: Number(document.getElementById("esp_port").value.trim()),
    vj3350_host: document.getElementById("vj3350_host").value.trim(),
    vj3350_port: Number(document.getElementById("vj3350_port").value.trim()),
    vj6530_host: document.getElementById("vj6530_host").value.trim(),
    vj6530_port: Number(document.getElementById("vj6530_port").value.trim())
  };
  await api("/api/config", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
  document.getElementById("dev_status").textContent = "saved (service restarted)";
}

showTok();
reloadAll();
</script>
</body></html>
        """

    # -----------------------------
    # Test UI (Clear output + log handling)
    # -----------------------------
    @app.get("/ui/test", response_class=HTMLResponse)
    def ui_test():
        # Minimal invasive: nur Buttons + Logs hinzufügen.
        return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>MAS-004 Test UI</title>
  <style>
    body{font-family:Arial; margin:20px; max-width:1100px}
    .row{display:flex; gap:10px; align-items:center; flex-wrap:wrap}
    input{padding:6px; margin:4px}
    button{padding:7px 10px; cursor:pointer}
    .tabs button{border:1px solid #aaa; border-radius:10px; background:#f3f3f3}
    .tabs button.active{background:#ddd}
    pre{background:#111; color:#eee; padding:10px; border-radius:10px; overflow:auto; max-height:420px}
    .muted{color:#666}
    select{padding:6px}
  </style>
</head>
<body>
  <h2>MAS-004 Test UI</h2>
  <p class="muted">Tabs simulieren Komponente. Send aus ESP/LASER/TTO läuft über Raspi -> Mikrotom.</p>

  <div class="row">
    <label>UI Token:</label>
    <input id="token" style="width:420px" placeholder="MAS004-..."/>
    <button onclick="saveToken()">Save</button>
    <span id="tokstate" class="muted"></span>
    <a href="/" style="margin-left:auto">Home</a>
  </div>

  <div class="tabs row" style="margin-top:10px">
    <button id="tab_raspi" onclick="setTab('raspi')">RASPI</button>
    <button id="tab_esp" onclick="setTab('esp')">ESP-PLC</button>
    <button id="tab_laser" onclick="setTab('laser')">VJ3350</button>
    <button id="tab_tto" onclick="setTab('tto')">VJ6530</button>
  </div>

  <div class="row" style="margin-top:10px">
    <input id="cmd" style="width:520px" placeholder="z.B. TTP00002=?  oder  TTE1000=1  oder  MAS0026=20"/>
    <button onclick="send()">Send</button>
    <button onclick="clearOut()">Clear Output</button>
    <span id="status" class="muted"></span>
  </div>

  <h3>Output</h3>
  <pre id="out"></pre>

  <h3>Logs</h3>
  <div class="row">
    <label>Channel:</label>
    <select id="logch"></select>
    <button onclick="loadLogs()">Reload Logs</button>
    <button onclick="downloadLog()">Download .log</button>
    <button onclick="clearLog()">Clear Log</button>
    <span id="log_status" class="muted"></span>
  </div>
  <pre id="logview"></pre>

<script>
let currentTab = "raspi";

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
function ts(){ return new Date().toISOString().replace('T',' ').replace('Z',''); }
function logLine(s){
  const out = document.getElementById("out");
  out.textContent += s + "\\n";
  out.scrollTop = out.scrollHeight;
}
function clearOut(){
  document.getElementById("out").textContent = "";
}
function setTab(t){
  currentTab = t;
  for(const id of ["raspi","esp","laser","tto"]){
    document.getElementById("tab_"+id).classList.toggle("active", id===t);
  }
}
async function send(){
  const cmd = document.getElementById("cmd").value.trim();
  if(!cmd) return;
  document.getElementById("status").textContent = "sending...";
  // Wir enqueue'n eine Message an Mikrotom (über Raspi Outbox) oder nutzen deinen existierenden Endpoint,
  // hier simpel: outbox/enqueue -> Mikrotom inbox (dein Router reagiert auf Mikrotom->Raspi; fürs Testen reicht die Simulation).
  const payload = {
    method: "POST",
    path: "/api/inbox",
    headers: {},
    body: { msg: cmd, source: currentTab }
  };
  const j = await api("/api/outbox/enqueue", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
  logLine(`[${ts()}] OUT  manual -> ${currentTab}: ${cmd}  (idem=${j.idempotency_key})`);
  document.getElementById("status").textContent = "ok";
}
async function loadChannels(){
  const j = await api("/api/ui/logs/channels");
  const sel = document.getElementById("logch");
  sel.innerHTML = "";
  for(const ch of j.channels){
    const o = document.createElement("option");
    o.value = ch; o.textContent = ch;
    sel.appendChild(o);
  }
  if(!sel.value && j.channels.length) sel.value = j.channels[0];
}
async function loadLogs(){
  document.getElementById("log_status").textContent = "loading...";
  const ch = document.getElementById("logch").value;
  const j = await api(`/api/ui/logs?channel=${encodeURIComponent(ch)}&limit=400`);
  const lines = j.items.map(it => {
    const d = new Date(it.ts*1000);
    const t = d.toISOString().replace('T',' ').replace('Z','');
    return `[${t}] ${String(it.direction||"").toUpperCase()}  ${it.message}`;
  }).join("\\n");
  document.getElementById("logview").textContent = lines;
  document.getElementById("log_status").textContent = "ok";
}
async function clearLog(){
  const ch = document.getElementById("logch").value;
  if(!confirm("Log löschen: "+ch+" ?")) return;
  await api(`/api/ui/logs/clear?channel=${encodeURIComponent(ch)}`, {method:"POST"});
  await loadLogs();
}
async function downloadLog(){
  const ch = document.getElementById("logch").value;
  const t = getToken();
  const r = await fetch(`/api/ui/logs/download?channel=${encodeURIComponent(ch)}`, {headers: t?{"X-Token":t}:{}} );
  if(!r.ok){ alert(await r.text()); return; }
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = ch + ".log";
  a.click();
  URL.revokeObjectURL(a.href);
}

showTok();
setTab("raspi");
loadChannels().then(loadLogs);
</script>
</body></html>
        """

    return app