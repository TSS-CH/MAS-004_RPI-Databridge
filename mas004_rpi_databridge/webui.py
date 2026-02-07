from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Header, UploadFile, File, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
import json
import subprocess
import os
import re
import tempfile
import uuid

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


class TestSendReq(BaseModel):
    source: str
    msg: str
    ptype_hint: Optional[str] = None


def build_app(cfg_path: str = DEFAULT_CFG_PATH) -> FastAPI:
    app = FastAPI(title="MAS-004_RPI-Databridge", version="0.3.0")
    
    cfg = Settings.load(cfg_path)
    db = DB(cfg.db_path)
    outbox = Outbox(db)
    inbox = Inbox(db)
    params = ParamStore(db)
    logs = LogStore(db)
    test_sources = {"raspi", "esp-plc", "vj3350", "vj6530"}
    default_ptype_hint = {"raspi": "", "esp-plc": "MAS", "vj3350": "LSE", "vj6530": "TTE"}

    def normalize_test_source(source: str) -> str:
        s = (source or "").strip().lower()
        if s not in test_sources:
            raise HTTPException(status_code=400, detail=f"Unknown source '{source}'")
        return s

    def normalize_test_line(raw_msg: str, ptype_hint: Optional[str]) -> str:
        s = (raw_msg or "").strip()
        if not s:
            raise HTTPException(status_code=400, detail="Empty message")

        m_full = re.match(r"^\s*([A-Za-z]{3})([0-9A-Za-z_]+)\s*=\s*(.+?)\s*$", s)
        if m_full:
            return f"{m_full.group(1).upper()}{m_full.group(2)}={m_full.group(3).strip()}"

        hint = (ptype_hint or "").strip().upper()
        if hint and not re.match(r"^[A-Z]{3}$", hint):
            raise HTTPException(status_code=400, detail="ptype_hint must be 3 letters (e.g. TTE, MAP, MAS)")

        m_short = re.match(r"^\s*([0-9A-Za-z_]+)\s*=\s*(.+?)\s*$", s)
        if m_short and hint:
            return f"{hint}{m_short.group(1)}={m_short.group(2).strip()}"

        return s

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
    # Test helper API (manual simulation from UI windows)
    # -----------------------------
    @app.post("/api/test/send")
    def api_test_send(req: TestSendReq, x_token: Optional[str] = Header(default=None)):
        cfg2 = Settings.load(cfg_path)
        require_token(x_token, cfg2)

        src = normalize_test_source(req.source)
        hint = req.ptype_hint if req.ptype_hint is not None else default_ptype_hint.get(src, "")
        line = normalize_test_line(req.msg, hint)

        url = cfg2.peer_base_url.rstrip("/") + "/api/inbox"
        headers = {}

        if src == "raspi":
            logs.log("raspi", "out", f"manual->mikrotom: {line}")
            idem = outbox.enqueue("POST", url, headers, {"msg": line, "source": "raspi"}, None)
            return {
                "ok": True,
                "source": src,
                "line": line,
                "route": "raspi->mikrotom",
                "ack": "ACK_QUEUED",
                "idempotency_key": idem,
            }

        logs.log(src, "out", f"manual->raspi: {line}")
        logs.log("raspi", "in", f"{src}: {line}")
        idem = outbox.enqueue(
            "POST",
            url,
            headers,
            {"msg": line, "source": "raspi", "origin": src},
            None,
        )
        logs.log("raspi", "out", f"forward to mikrotom: {line}")
        return {
            "ok": True,
            "source": src,
            "line": line,
            "route": f"{src}->raspi->mikrotom",
            "ack": "ACK_QUEUED",
            "idempotency_key": idem,
        }

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

        raw_body = await request.body()
        body = None
        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except Exception:
                txt = raw_body.decode("utf-8", errors="replace").strip()
                body = {"msg": txt} if txt else None

        headers = dict(request.headers)
        idem = x_idempotency_key or headers.get("x-idempotency-key") or str(uuid.uuid4())
        source = request.client.host if request.client else None
        if isinstance(body, dict):
            src = body.get("source")
            if isinstance(src, str) and src.strip():
                source = src.strip()
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

    # ========================
    # ===== LOG API ==========
    # ========================
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
const LS_KEY = "mas004_ui_token";

function lsGet(k){
  try { return localStorage.getItem(k) || ""; } catch(e){ return ""; }
}
function lsSet(k,v){
  try { localStorage.setItem(k, v); } catch(e){}
}

function cookieGet(name){
  const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()\\[\\]\\\\\\/\\+^])/g, '\\\\$1') + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : "";
}
function cookieSet(name, val){
  document.cookie = `${name}=${encodeURIComponent(val)}; Path=/; Max-Age=${60*60*24*365}; SameSite=Lax`;
}

function getToken(){
  return lsGet(LS_KEY) || cookieGet(LS_KEY) || "";
}

function saveToken(){
  const v = document.getElementById("token").value.trim();
  lsSet(LS_KEY, v);
  cookieSet(LS_KEY, v);
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
  const nmin = prompt(`min_v fuer ${pkey}`, minv);
  if(nmin === null) return;
  const nmax = prompt(`max_v fuer ${pkey}`, maxv);
  if(nmax === null) return;
  const ndef = prompt(`default_v fuer ${pkey}`, defv);
  if(ndef === null) return;
  const nrw = prompt(`rw fuer ${pkey} (R / W / R/W)`, rw);
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
  if(!f){ alert("Bitte .xlsx auswaehlen"); return; }
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

  :root{
    --vj-navy:#003A70;
    --vj-blue:#005EB8;
    --vj-red:#E4002B;
    --bg:#F5F7FA;
    --card:#FFFFFF;
    --text:#1D232B;
    --muted:#5F6B7A;
    --border:#D8E0EA;
    --radius:14px;
    --shadow:0 6px 24px rgba(0,0,0,.08);
  }
  html,body{height:100%;}
  body{
    margin:0;
    font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,sans-serif;
    background:var(--bg);
    color:var(--text);
  }
  header{
    background:linear-gradient(90deg,var(--vj-navy),var(--vj-blue));
    color:#fff;
    padding:14px 18px;
    box-shadow:0 2px 14px rgba(0,0,0,.15);
    position:sticky;
    top:0;
    z-index:10;
  }
  header .row{
    display:flex;
    align-items:center;
    gap:14px;
    justify-content:space-between;
    flex-wrap:wrap;
  }
  .brand{
    display:flex;
    align-items:center;
    gap:12px;
  }
  .badge{
    font-size:12px;
    padding:4px 8px;
    border:1px solid rgba(255,255,255,.35);
    border-radius:999px;
    opacity:.95;
  }
  main{padding:18px; max-width:1200px; margin:0 auto;}
  .grid{
    display:grid;
    grid-template-columns:repeat(12,1fr);
    gap:14px;
  }
  .card{
    grid-column:span 12;
    background:var(--card);
    border:1px solid var(--border);
    border-radius:var(--radius);
    box-shadow:var(--shadow);
    padding:16px;
  }
  .card h2{margin:0 0 10px 0; font-size:18px;}
  .sub{color:var(--muted); font-size:13px; margin-top:2px;}
  .row{display:flex; gap:10px; flex-wrap:wrap; align-items:center;}
  label{font-size:12px; color:var(--muted);}
  input,select,textarea{
    width:100%;
    padding:10px 12px;
    border:1px solid var(--border);
    border-radius:12px;
    background:#fff;
    font-size:14px;
    outline:none;
  }
  textarea{min-height:110px; font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
  input:focus,select:focus,textarea:focus{border-color:var(--vj-blue); box-shadow:0 0 0 3px rgba(0,94,184,.15);}
  .btn{
    appearance:none;
    border:0;
    cursor:pointer;
    padding:10px 14px;
    border-radius:12px;
    font-weight:600;
    font-size:14px;
  }
  .btn.primary{background:var(--vj-blue); color:#fff;}
  .btn.danger{background:var(--vj-red); color:#fff;}
  .btn.ghost{background:#fff; border:1px solid rgba(255,255,255,.45); color:#fff;}
  .btn:active{transform:translateY(1px);}
  .pill{
    display:inline-flex;
    align-items:center;
    gap:8px;
    padding:8px 10px;
    border:1px solid var(--border);
    border-radius:999px;
    font-size:13px;
    background:#fff;
  }
  .ok{color:#0B7A3B; font-weight:700;}
  .bad{color:var(--vj-red); font-weight:700;}
  .mono{font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
  .split{display:grid; grid-template-columns:repeat(12,1fr); gap:12px;}
  .col6{grid-column:span 6;}
  .col4{grid-column:span 4;}
  .col8{grid-column:span 8;}
  @media(max-width:900px){.col6,.col4,.col8{grid-column:span 12;}}

</style>
</head>
<body>
  <h2>System Settings</h2>
  <p class="muted">
    Token wird im Browser gespeichert (localStorage). Aenderungen an Network koennen dich aussperren - daher "Apply now" bewusst setzen.
    <br/>Hinweis: Subnet-Maske (z.B. 255.255.255.0) und Prefix (z.B. /24) sind identisch - nur andere Schreibweise.
  </p>

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
      <label style="width:70px">eth0 IP</label><input id="eth0_ip" style="width:160px"/>
      <label>Subnet</label><input id="eth0_mask" style="width:160px" placeholder="255.255.255.0" oninput="maskChanged('eth0')"/>
      <label>Prefix</label><input id="eth0_pre" style="width:60px" placeholder="24" oninput="prefixChanged('eth0')"/>
      <label>GW</label><input id="eth0_gw" style="width:160px"/>
    </div>

    <div class="row">
      <label style="width:70px">eth1 IP</label><input id="eth1_ip" style="width:160px"/>
      <label>Subnet</label><input id="eth1_mask" style="width:160px" placeholder="255.255.255.0" oninput="maskChanged('eth1')"/>
      <label>Prefix</label><input id="eth1_pre" style="width:60px" placeholder="24" oninput="prefixChanged('eth1')"/>
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

// ---------- Mask <-> Prefix ----------
function prefixToMask(prefix){
  const p = Number(prefix);
  if(!Number.isInteger(p) || p < 0 || p > 32) return null;
  let mask = 0 >>> 0;
  if(p === 0) mask = 0;
  else mask = (0xFFFFFFFF << (32 - p)) >>> 0;
  const a = (mask >>> 24) & 255;
  const b = (mask >>> 16) & 255;
  const c = (mask >>> 8) & 255;
  const d = mask & 255;
  return `${a}.${b}.${c}.${d}`;
}

function maskToPrefix(maskStr){
  const parts = (maskStr||"").trim().split(".");
  if(parts.length !== 4) return null;
  const nums = parts.map(x => Number(x));
  if(nums.some(n => !Number.isInteger(n) || n < 0 || n > 255)) return null;

  let m = ((nums[0]<<24)>>>0) | ((nums[1]<<16)>>>0) | ((nums[2]<<8)>>>0) | (nums[3]>>>0);

  // contiguous ones then zeros check
  let seenZero = false;
  let prefix = 0;
  for(let i=31;i>=0;i--){
    const bit = (m >>> i) & 1;
    if(bit === 1){
      if(seenZero) return null;
      prefix++;
    }else{
      seenZero = true;
    }
  }
  return prefix;
}

function setBad(el, bad){
  if(bad) el.classList.add("bad");
  else el.classList.remove("bad");
}

function maskChanged(iface){
  const maskEl = document.getElementById(`${iface}_mask`);
  const preEl  = document.getElementById(`${iface}_pre`);
  const p = maskToPrefix(maskEl.value);
  if(p === null){
    setBad(maskEl, true);
  }else{
    setBad(maskEl, false);
    preEl.value = String(p);
    setBad(preEl, false);
  }
}

function prefixChanged(iface){
  const maskEl = document.getElementById(`${iface}_mask`);
  const preEl  = document.getElementById(`${iface}_pre`);
  const m = prefixToMask(preEl.value);
  if(m === null){
    setBad(preEl, true);
  }else{
    setBad(preEl, false);
    maskEl.value = m;
    setBad(maskEl, false);
  }
}

function effectivePrefix(iface){
  // bevorzugt: aus Maske berechnen (wenn gueltig)
  const mask = document.getElementById(`${iface}_mask`).value.trim();
  if(mask){
    const p = maskToPrefix(mask);
    if(p !== null) return p;
  }
  // fallback: Prefix-Feld
  const pre = Number(document.getElementById(`${iface}_pre`).value.trim());
  if(Number.isInteger(pre) && pre >= 0 && pre <= 32) return pre;
  return null;
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
  document.getElementById("eth0_pre").value = n.eth0_subnet || "";     // bei dir ist das "Subnet" intern Prefix-String
  prefixChanged("eth0");                                               // fuellt Mask automatisch
  document.getElementById("eth0_gw").value = n.eth0_gateway || "";

  document.getElementById("eth1_ip").value = n.eth1_ip || "";
  document.getElementById("eth1_pre").value = n.eth1_subnet || "";
  prefixChanged("eth1");
  document.getElementById("eth1_gw").value = n.eth1_gateway || "";

  document.getElementById("netinfo").textContent = JSON.stringify(net.status, null, 2);
}

async function saveNetwork(){
  document.getElementById("net_status").textContent = "saving...";

  const p0 = effectivePrefix("eth0");
  const p1 = effectivePrefix("eth1");
  if(p0 === null || p1 === null){
    alert("Subnet/Prefix ungueltig. Bitte Maske (z.B. 255.255.255.0) oder Prefix (0..32) korrekt setzen.");
    document.getElementById("net_status").textContent = "ERROR";
    return;
  }

  const payload = {
    eth0_ip: document.getElementById("eth0_ip").value.trim(),
    eth0_prefix: p0,
    eth0_gateway: document.getElementById("eth0_gw").value.trim(),
    eth1_ip: document.getElementById("eth1_ip").value.trim(),
    eth1_prefix: p1,
    eth1_gateway: document.getElementById("eth1_gw").value.trim(),
    apply_now: document.getElementById("apply_now").checked
  };

  const j = await api("/api/system/network", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });

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
    # Test UI
    # -----------------------------
    @app.get("/ui/test", response_class=HTMLResponse)
    def ui_test():
        return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>MAS-004 Test UI</title>
  <style>
    :root{
      --bg:#f4f6f9;
      --card:#ffffff;
      --line:#d6dde7;
      --text:#1f2933;
      --muted:#5f6b7a;
      --blue:#005eb8;
      --green:#0f9d58;
      --red:#c62828;
    }
    body{margin:0; font-family:Segoe UI,Arial,sans-serif; background:var(--bg); color:var(--text)}
    .wrap{max-width:1500px; margin:0 auto; padding:16px}
    .row{display:flex; gap:10px; align-items:center; flex-wrap:wrap}
    .top{background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px}
    .grid{display:grid; gap:12px; grid-template-columns:repeat(2,minmax(0,1fr)); margin-top:12px}
    .card{background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px}
    .card h3{margin:0 0 8px 0}
    input,button{padding:8px 10px; border-radius:8px; border:1px solid var(--line)}
    input{background:#fff}
    button{cursor:pointer}
    button.primary{background:var(--blue); color:#fff; border-color:var(--blue)}
    button.danger{background:#fff; color:var(--red); border-color:var(--red)}
    .pill{padding:4px 8px; border:1px solid var(--line); border-radius:999px; font-size:12px}
    .ok{color:var(--green)}
    .err{color:var(--red)}
    pre{
      margin:8px 0 0 0;
      background:#f8fafc;
      border:1px solid var(--line);
      border-radius:8px;
      padding:10px;
      white-space:pre-wrap;
      max-height:220px;
      overflow:auto;
      font-size:12px;
      line-height:1.35;
    }
    .muted{color:var(--muted); font-size:12px}
    @media (max-width:1100px){ .grid{grid-template-columns:1fr;} }
  </style>
</head>
<body>
  <div class="wrap">
    <h2>MAS-004 Test UI</h2>
    <div class="top row">
      <label>UI Token:</label>
      <input id="token" style="width:360px" placeholder="MAS004-..."/>
      <button onclick="saveToken()">Save Token</button>
      <button onclick="reloadAll()">Reload All Logs</button>
      <span id="tokstate" class="pill">no token</span>
      <a href="/" style="margin-left:auto">Home</a>
    </div>

    <div class="grid">
      <section class="card">
        <h3>RASPI-PLC</h3>
        <div class="muted">Manual input goes directly to Mikrotom.</div>
        <div class="row" style="margin-top:8px">
          <label>ParamType hint</label>
          <input id="hint_raspi" style="width:90px" placeholder="optional" value=""/>
          <input id="cmd_raspi" style="flex:1; min-width:260px" placeholder="e.g. TTP00002=? or MAP0001=500"/>
          <button class="primary" onclick="sendFrom('raspi')">Send</button>
          <button onclick="clearOutput('raspi')">Clear Output</button>
          <span id="st_raspi" class="pill"></span>
        </div>
        <pre id="out_raspi"></pre>
        <div class="row" style="margin-top:8px">
          <button onclick="loadLogs('raspi')">Reload Log</button>
          <button onclick="downloadLog('raspi')">Download Log</button>
          <button class="danger" onclick="clearLog('raspi')">Clear Log</button>
          <span id="logst_raspi" class="pill"></span>
        </div>
        <pre id="log_raspi"></pre>
      </section>

      <section class="card">
        <h3>ESP-PLC</h3>
        <div class="muted">Manual input goes ESP-PLC -> RASPI -> Mikrotom.</div>
        <div class="row" style="margin-top:8px">
          <label>ParamType hint</label>
          <input id="hint_esp_plc" style="width:90px" value="MAS"/>
          <input id="cmd_esp_plc" style="flex:1; min-width:260px" placeholder="e.g. 0026=20 or MAP0001=500"/>
          <button class="primary" onclick="sendFrom('esp-plc')">Send</button>
          <button onclick="clearOutput('esp-plc')">Clear Output</button>
          <span id="st_esp_plc" class="pill"></span>
        </div>
        <pre id="out_esp_plc"></pre>
        <div class="row" style="margin-top:8px">
          <button onclick="loadLogs('esp-plc')">Reload Log</button>
          <button onclick="downloadLog('esp-plc')">Download Log</button>
          <button class="danger" onclick="clearLog('esp-plc')">Clear Log</button>
          <span id="logst_esp_plc" class="pill"></span>
        </div>
        <pre id="log_esp_plc"></pre>
      </section>

      <section class="card">
        <h3>VJ3350 (Laser)</h3>
        <div class="muted">Manual input goes VJ3350 -> RASPI -> Mikrotom.</div>
        <div class="row" style="margin-top:8px">
          <label>ParamType hint</label>
          <input id="hint_vj3350" style="width:90px" value="LSE"/>
          <input id="cmd_vj3350" style="flex:1; min-width:260px" placeholder="e.g. 1000=1 or LSW1000=1"/>
          <button class="primary" onclick="sendFrom('vj3350')">Send</button>
          <button onclick="clearOutput('vj3350')">Clear Output</button>
          <span id="st_vj3350" class="pill"></span>
        </div>
        <pre id="out_vj3350"></pre>
        <div class="row" style="margin-top:8px">
          <button onclick="loadLogs('vj3350')">Reload Log</button>
          <button onclick="downloadLog('vj3350')">Download Log</button>
          <button class="danger" onclick="clearLog('vj3350')">Clear Log</button>
          <span id="logst_vj3350" class="pill"></span>
        </div>
        <pre id="log_vj3350"></pre>
      </section>

      <section class="card">
        <h3>VJ6530 (TTO)</h3>
        <div class="muted">Manual input goes VJ6530 -> RASPI -> Mikrotom.</div>
        <div class="row" style="margin-top:8px">
          <label>ParamType hint</label>
          <input id="hint_vj6530" style="width:90px" value="TTE"/>
          <input id="cmd_vj6530" style="flex:1; min-width:260px" placeholder="e.g. 1000=1 or TTP00002=?"/>
          <button class="primary" onclick="sendFrom('vj6530')">Send</button>
          <button onclick="clearOutput('vj6530')">Clear Output</button>
          <span id="st_vj6530" class="pill"></span>
        </div>
        <pre id="out_vj6530"></pre>
        <div class="row" style="margin-top:8px">
          <button onclick="loadLogs('vj6530')">Reload Log</button>
          <button onclick="downloadLog('vj6530')">Download Log</button>
          <button class="danger" onclick="clearLog('vj6530')">Clear Log</button>
          <span id="logst_vj6530" class="pill"></span>
        </div>
        <pre id="log_vj6530"></pre>
      </section>
    </div>
  </div>

<script>
const TOKEN_KEY = "mas004_ui_token";
const SOURCES = ["raspi","esp-plc","vj3350","vj6530"];

function sid(source){ return String(source||"").replace(/-/g, "_"); }
function el(id){ return document.getElementById(id); }

function cookieGet(name){
  const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[-.$?*|{}()\\[\\]\\\\\\/\\+^]/g,'\\\\$&') + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : "";
}
function cookieSet(name, value, days){
  const d = new Date();
  d.setTime(d.getTime() + (days*24*60*60*1000));
  document.cookie = `${name}=${encodeURIComponent(value)}; expires=${d.toUTCString()}; path=/; SameSite=Lax`;
}
function getToken(){
  try{
    return localStorage.getItem(TOKEN_KEY) || cookieGet(TOKEN_KEY) || "";
  }catch(e){
    return cookieGet(TOKEN_KEY) || "";
  }
}
function saveToken(){
  const v = el("token").value.trim();
  try{ localStorage.setItem(TOKEN_KEY, v); }catch(e){}
  cookieSet(TOKEN_KEY, v, 3650);
  showTok();
}
function showTok(){
  const t = getToken();
  el("token").value = t;
  el("tokstate").textContent = t ? "token ok" : "no token";
}
async function api(path, opt={}){
  opt.headers = opt.headers || {};
  const t = getToken();
  if(t) opt.headers["X-Token"] = t;
  const r = await fetch(path, opt);
  const txt = await r.text();
  let j = null;
  try{ j = JSON.parse(txt); }catch(e){}
  if(!r.ok){
    throw new Error((j && j.detail) ? j.detail : ("HTTP " + r.status + " " + txt));
  }
  return j;
}
function ts(){ return new Date().toISOString().replace("T"," ").replace("Z",""); }
function setStatus(source, msg, isErr=false){
  const node = el(`st_${sid(source)}`);
  node.textContent = msg || "";
  node.className = "pill " + (isErr ? "err" : "ok");
}
function setLogStatus(source, msg, isErr=false){
  const node = el(`logst_${sid(source)}`);
  node.textContent = msg || "";
  node.className = "pill " + (isErr ? "err" : "ok");
}
function appendOutput(source, line){
  const node = el(`out_${sid(source)}`);
  node.textContent += line + "\\n";
  node.scrollTop = node.scrollHeight;
}
function clearOutput(source){
  el(`out_${sid(source)}`).textContent = "";
}
function formatLogs(items){
  return items.map(it => {
    const d = new Date((it.ts || 0) * 1000);
    const t = d.toISOString().replace("T"," ").replace("Z","");
    const dir = String(it.direction || "").toUpperCase();
    return `[${t}] ${dir} ${it.message || ""}`;
  }).join("\\n");
}
async function sendFrom(source){
  const s = sid(source);
  const cmdEl = el(`cmd_${s}`);
  const hintEl = el(`hint_${s}`);
  const msg = (cmdEl.value || "").trim();
  if(!msg){
    setStatus(source, "empty", true);
    return;
  }
  setStatus(source, "sending...");
  try{
    const payload = {
      source: source,
      msg: msg,
      ptype_hint: (hintEl && hintEl.value) ? hintEl.value.trim() : ""
    };
    const j = await api("/api/test/send", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload)
    });
    appendOutput(source, `[${ts()}] ${j.route}: ${j.line} (${j.ack}, idem=${j.idempotency_key})`);
    if(source !== "raspi"){
      appendOutput("raspi", `[${ts()}] incoming from ${source}: ${j.line}`);
    }
    cmdEl.value = "";
    setStatus(source, "ok");
    await Promise.all([loadLogs(source), loadLogs("raspi")]);
  }catch(e){
    setStatus(source, "ERROR: " + e.message, true);
  }
}
async function loadLogs(source){
  setLogStatus(source, "loading...");
  try{
    const j = await api(`/api/ui/logs?channel=${encodeURIComponent(source)}&limit=350`);
    el(`log_${sid(source)}`).textContent = formatLogs(j.items || []);
    setLogStatus(source, "ok");
  }catch(e){
    setLogStatus(source, "ERROR: " + e.message, true);
  }
}
async function clearLog(source){
  if(!confirm("Clear log: " + source + " ?")) return;
  try{
    await api(`/api/ui/logs/clear?channel=${encodeURIComponent(source)}`, {method:"POST"});
    await loadLogs(source);
  }catch(e){
    setLogStatus(source, "ERROR: " + e.message, true);
  }
}
async function downloadLog(source){
  const t = getToken();
  const r = await fetch(`/api/ui/logs/download?channel=${encodeURIComponent(source)}`, {headers: t ? {"X-Token":t} : {}});
  if(!r.ok){
    alert(await r.text());
    return;
  }
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = source + ".log";
  a.click();
  URL.revokeObjectURL(a.href);
}
async function reloadAll(){
  const jobs = SOURCES.map(src => loadLogs(src));
  await Promise.all(jobs);
}

showTok();
reloadAll();
</script>
</body></html>
"""

    return app


