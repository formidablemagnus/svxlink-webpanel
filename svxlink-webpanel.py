#!/usr/bin/env python3
"""SvxLink status and log web panel. Python standard library only."""
import base64
import html
import hmac
import json
import tempfile
import os
import re
import subprocess
import ssl
import time
import urllib.request
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = os.environ.get("SVXLINK_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("SVXLINK_WEB_PORT", "8090"))
LOGFILE = os.environ.get("SVXLINK_LOGFILE", "/var/log/svxlink")
SERVICE = os.environ.get("SVXLINK_SERVICE", "svxlink")
USERNAME = os.environ.get("SVXLINK_WEB_USER", "admin")
PASSWORD = os.environ.get("SVXLINK_WEB_PASSWORD", "")
MAX_LOG_BYTES = 256 * 1024
MAX_LINES = 250
BUTTONS_FILE = os.environ.get("SVXLINK_BUTTONS_FILE", "/var/lib/svxlink/webpanel-buttons.json")
REFLECTOR_PROXY_URL = os.environ.get("SVXLINK_REFLECTOR_PROXY_URL", "https://reflector-sm.svxlink.org/reflector_proxy")

PAGE = r"""<!doctype html>
<html lang="sv"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SvxLink Control</title>
<style>
:root{color-scheme:dark;--bg:#0b1220;--panel:#131e30;--line:#26364d;--text:#e7edf6;--muted:#9baec6;--green:#55d6a0;--red:#ff7d86;--amber:#ffd166}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1200px;margin:auto;padding:22px}header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:1.55rem;margin:0}h2{font-size:1rem;margin:0 0 14px}.muted{color:var(--muted);font-size:.88rem}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:17px;margin-bottom:14px}
.k{color:var(--muted);font-size:.82rem}.v{font-size:1.35rem;font-weight:700;margin-top:5px;overflow-wrap:anywhere}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--amber);margin-right:8px}.ok{background:var(--green)}.bad{background:var(--red)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}input,select,button{background:#0b1424;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:9px 11px;font:inherit}
button{cursor:pointer}button:hover{border-color:#6b89b2}input{flex:1;min-width:170px}
.log{height:55vh;min-height:300px;overflow:auto;background:#080e18;border:1px solid var(--line);border-radius:9px;padding:12px;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
.logline{padding:2px 0;border-bottom:1px solid #152033}.warn{color:var(--amber)}.err{color:var(--red)}.event{color:#8dc7ff}
footer{color:var(--muted);font-size:.8rem;margin-top:12px}
@media(max-width:700px){main{padding:13px}.grid{grid-template-columns:1fr}.v{font-size:1.2rem}.log{height:50vh}}
</style></head><body><main>
<header><div><h1>SvxLink Control</h1><div class="muted">Status, händelser och loggar</div></div><div class="muted">Uppdateras automatiskt</div></header>
<div class="grid">
<section class="card"><div class="k">SvxLink-tjänst</div><div class="v" id="service"><span class="dot"></span>Hämtar…</div></section>
<section class="card"><div class="k">Senaste talgruppshändelse</div><div class="v" id="tg">—</div><div class="muted" id="tgtime"></div></section>
<section class="card"><div class="k">Aktuell vald talgrupp</div><div class="v" id="currenttg">Okänd</div><div class="muted">Baserad på senaste ”Selecting TG” i loggen</div></section>
<section class="card"><div class="k">Senaste reflektorhändelse</div><div class="v" id="node">—</div><div class="muted" id="nodetime"></div></section>
<section class="card"><div class="k">Loggöversikt</div><div class="v" id="counts">—</div><div class="muted" id="countdetail">Fel · varningar · händelser</div></section>
</div>
<section class="card"><h2>Talgrupper på reflektorn</h2>
<div class="muted" style="margin-bottom:10px">Talgrupper som noder har valt just nu. TX anger noder som rapporterar pågående sändning. Klicka för att välja talgrupp.</div>
<div class="toolbar" id="dynamic-tg-buttons"><span class="muted">Hämtar reflektorstatus…</span></div>
<div class="muted" id="reflector-updated"></div>
</section>
<section class="card"><h2>Styrning</h2>
<div class="muted" style="margin-bottom:10px">Välj en sparad snabbknapp eller koppla från reflektorn. Kommandon skickas direkt.</div>
<div class="toolbar" id="tgbuttons"></div>
<div class="toolbar">
<button id="managebuttons">Administrera knappar</button>
<button id="disconnect" style="border-color:var(--red);color:var(--red)">Koppla från reflektorn</button>
</div>
<div id="buttonadmin" class="card" style="display:none;margin-top:10px">
<h2>Administrera DTMF-knappar</h2>
<div class="muted">Lägg till, ändra eller ta bort snabbknappar. Kommandon skickas direkt när du trycker på knappen.</div>
<div id="buttonrows"></div>
<div class="toolbar"><button id="addbutton">Lägg till knapp</button><button id="savebuttons">Spara knappar</button><button id="cancelbuttons">Stäng</button></div>
<div class="muted" id="adminresult" role="status"></div>
</div>
<div class="toolbar" style="margin-top:10px">
<input id="customdtmf" maxlength="20" placeholder="Eget DTMF-kommando (0–9, * och #)" aria-label="Eget DTMF-kommando">
<button id="senddtmf">Skicka DTMF</button>
</div>
<div class="muted">Endast siffrorna 0–9 samt * och # tillåts, högst 20 tecken.</div>
<div class="muted" id="commandresult" role="status"></div></section>
<section class="card"><h2>Loggar</h2><div class="toolbar">
<input id="filter" placeholder="Filtrera loggar (t.ex. UDP, ERROR, SA7MHN)">
<select id="kind"><option value="all">Alla nivåer</option><option value="error">Fel</option><option value="warn">Varningar</option><option value="event">Reflektor / talgrupp</option></select>
<button id="refresh">Uppdatera nu</button></div><div class="log" id="logs">Hämtar loggar…</div>
<footer id="updated">Väntar på första uppdateringen.</footer></section>
<footer>Denna panel skickar inga kommandon till radion. Loggstatus kan släpa efter faktisk aktivitet.</footer>
</main><script>
const $=id=>document.getElementById(id);
function esc(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
let allLines=[];
function level(s){if(/ERROR|FATAL|CRITICAL/i.test(s))return'err';if(/WARNING|UDP frame\(s\) lost/i.test(s))return'warn';if(/ReflectorLogic|Talker start|Talker stop|Node joined|Node left|Selecting TG/i.test(s))return'event';return'';}
function renderLogs(){const f=$('filter').value.toLowerCase(),k=$('kind').value;
 const filtered=allLines.filter(s=>(!f||s.toLowerCase().includes(f))&&(k==='all'||(k==='error'&&level(s)==='err')||(k==='warn'&&level(s)==='warn')||(k==='event'&&level(s)==='event')));
 $('logs').innerHTML=filtered.slice().reverse().map(s=>'<div class="logline '+level(s)+'">'+esc(s)+'</div>').join('')||'Inga loggrader matchar filtret.';
 }
async function update(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json();
 $('service').innerHTML='<span class="dot '+(d.active?'ok':'bad')+'"></span>'+esc(d.service_status);
 $('tg').textContent=d.tg_event||'Ingen identifierad händelse';$('tgtime').textContent=d.tg_time||'';
 $('currenttg').textContent=d.current_tg ? ('TG '+d.current_tg) : 'Okänd';
 $('node').textContent=d.node_event||'Ingen identifierad händelse';$('nodetime').textContent=d.node_time||'';
 $('counts').textContent=d.errors+' · '+d.warnings+' · '+d.events;
 allLines=d.lines||[];renderLogs();$('updated').textContent='Uppdaterad '+new Date().toLocaleTimeString('sv-SE')+' · visar '+allLines.length+' senaste loggrader';
 }catch(e){$('service').innerHTML='<span class="dot bad"></span>Kan inte läsa status';$('updated').textContent='Fel vid uppdatering: '+e.message;}}
async function updateReflector(){
 try{
  const r=await fetch('/api/reflector',{cache:'no-store'});const d=await r.json();
  if(!r.ok||!d.ok)throw Error(d.error||('HTTP '+r.status));
  const groups=d.talkgroups||[];
  $('dynamic-tg-buttons').innerHTML=groups.length?groups.map(tg=>'<button data-live-tg="'+esc(String(tg.tg))+'">TG '+esc(String(tg.tg))+' · '+tg.nodes+' nod'+(tg.nodes===1?'':'er')+(tg.talkers?' · TX '+tg.talkers:'')+'</button>').join(''):'<span class="muted">Inga noder har valt en talgrupp över 0 just nu.</span>';
  $('dynamic-tg-buttons').querySelectorAll('[data-live-tg]').forEach(el=>el.addEventListener('click',()=>sendCommand('tg:'+el.dataset.liveTg)));
  $('reflector-updated').textContent='Reflektorstatus uppdaterad '+new Date().toLocaleTimeString('sv-SE')+' · '+d.node_count+' synliga noder';
 }catch(e){$('dynamic-tg-buttons').innerHTML='<span class="muted">Kunde inte läsa reflektorstatus: '+esc(e.message)+'</span>';}
}
let buttonDefinitions=[];
function renderCommandButtons(){ $('tgbuttons').innerHTML=buttonDefinitions.map((b,i)=>'<button data-command-index="'+i+'">'+esc(b.label)+'</button>').join('');$('tgbuttons').querySelectorAll('[data-command-index]').forEach(el=>el.addEventListener('click',()=>sendCommand('dtmf:'+buttonDefinitions[Number(el.dataset.commandIndex)].command))); }
function renderAdmin(){ $('buttonrows').innerHTML=buttonDefinitions.map((b,i)=>'<div class="toolbar" style="align-items:center"><input data-label="'+i+'" maxlength="40" placeholder="Knappnamn" value="'+esc(b.label)+'"><input data-code="'+i+'" maxlength="20" placeholder="DTMF" value="'+esc(b.command)+'"><button data-delete="'+i+'">Ta bort</button></div>').join(''); $('buttonrows').querySelectorAll('[data-delete]').forEach(el=>el.addEventListener('click',()=>{buttonDefinitions.splice(Number(el.dataset.delete),1);renderAdmin();})); }
async function loadButtons(){try{const r=await fetch('/api/buttons',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||'Kunde inte läsa knappar');buttonDefinitions=d.buttons||[];renderCommandButtons();}catch(e){$('commandresult').textContent='Kunde inte läsa snabbknappar: '+e.message;}}
async function saveButtons(){const rows=[...$('buttonrows').querySelectorAll('[data-label]')];const next=rows.map(el=>({label:el.value,command:$('buttonrows').querySelector('[data-code="'+el.dataset.label+'"]').value}));try{const r=await fetch('/api/buttons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({buttons:next}),cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Kunde inte spara');buttonDefinitions=d.buttons;renderCommandButtons();renderAdmin();$('adminresult').textContent='Knapparna sparades.';}catch(e){$('adminresult').textContent='Fel: '+e.message;}}
async function sendCommand(action){const result=$('commandresult');result.textContent='Skickar kommando…';
 try{const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action}),cache:'no-store'});
 const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||('HTTP '+r.status));result.textContent=d.message;
 setTimeout(update,700);
 }catch(e){result.textContent='Kunde inte skicka kommandot: '+e.message;}}
document.querySelectorAll('[data-tg]').forEach(b=>b.addEventListener('click',()=>sendCommand('tg:'+b.dataset.tg)));
$('disconnect').addEventListener('click',()=>sendCommand('disconnect'));
$('managebuttons').addEventListener('click',()=>{$('buttonadmin').style.display=$('buttonadmin').style.display==='none'?'block':'none';renderAdmin();});
$('addbutton').addEventListener('click',()=>{buttonDefinitions.push({label:'Ny knapp',command:''});renderAdmin();});
$('savebuttons').addEventListener('click',saveButtons);
$('cancelbuttons').addEventListener('click',()=>{$('buttonadmin').style.display='none';loadButtons();});
$('senddtmf').addEventListener('click',()=>{
 const value=$('customdtmf').value.trim();
 if(!value){$('commandresult').textContent='Skriv ett DTMF-kommando först.';return;}
 sendCommand('dtmf:'+value);
});
$('customdtmf').addEventListener('keydown',e=>{if(e.key==='Enter')$('senddtmf').click();});
$('filter').addEventListener('input',renderLogs);$('kind').addEventListener('change',renderLogs);$('refresh').addEventListener('click',update);
loadButtons();update();updateReflector();setInterval(update,5000);setInterval(updateReflector,15000);
</script></body></html>"""

def service_status():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE],
            capture_output=True, text=True, timeout=2, check=False
        )
        value = (result.stdout or result.stderr).strip() or "okänt"
        return value, value == "active"
    except Exception:
        return "okänt", False

def read_log():
    try:
        with open(LOGFILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - MAX_LOG_BYTES))
            raw = f.read(MAX_LOG_BYTES)
        text = raw.decode("utf-8", errors="replace")
        # SvxLink logs may contain NULs or other control bytes; remove them before display.
        cleaned = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in text)
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        return lines[-MAX_LINES:]
    except Exception as e:
        return [f"Kunde inte läsa loggfilen {LOGFILE}: {e}"]

def latest_match(lines, pattern, formatter):
    rx = re.compile(pattern, re.IGNORECASE)
    for line in reversed(lines):
        m = rx.search(line)
        if m:
            return formatter(m), line[:24].strip()
    return "", ""

DTMF_PTY = os.environ.get("SVXLINK_DTMF_PTY", "/dev/shm/dtmf_ctrl")
ALLOWED_TGS = {"240", "2400", "2401", "2402", "2403", "2404", "2405", "2406", "2407", "24075", "9"}

DEFAULT_BUTTONS = [
    {"label": "TG 240", "command": "91240#"},
    {"label": "TG 2400", "command": "912400#"},
    {"label": "TG 2401", "command": "912401#"},
    {"label": "TG 2402", "command": "912402#"},
    {"label": "TG 2403", "command": "912403#"},
    {"label": "TG 2404", "command": "912404#"},
    {"label": "TG 2405", "command": "912405#"},
    {"label": "TG 2406", "command": "912406#"},
    {"label": "TG 2407", "command": "912407#"},
    {"label": "TG 24075", "command": "9124075#"},
    {"label": "TG 9", "command": "919#"},
]

def validate_buttons(buttons):
    if not isinstance(buttons, list) or len(buttons) > 30:
        raise ValueError("Ange högst 30 knappar.")
    clean = []
    for item in buttons:
        if not isinstance(item, dict):
            raise ValueError("Ogiltig knappdefinition.")
        label = str(item.get("label", "")).strip()
        command = str(item.get("command", "")).strip()
        if not label or len(label) > 40:
            raise ValueError("Knappnamn måste vara 1–40 tecken.")
        if not command or len(command) > 20 or not re.fullmatch(r"[0-9*#]+", command):
            raise ValueError("DTMF måste vara 1–20 tecken: 0–9, * eller #.")
        clean.append({"label": label, "command": command})
    return clean

def load_buttons():
    try:
        with open(BUTTONS_FILE, "r", encoding="utf-8") as f:
            return validate_buttons(json.load(f))
    except FileNotFoundError:
        return DEFAULT_BUTTONS

def save_buttons(buttons):
    clean = validate_buttons(buttons)
    folder = os.path.dirname(BUTTONS_FILE)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".svxlink-buttons-", dir=folder, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o640)
        os.replace(tmp, BUTTONS_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return clean

def send_dtmf(command):
    # Only validated DTMF strings are accepted.
    if command == "disconnect":
        digits = "9#"
        message = "Frånkopplingskommando skickat (9#)."
    elif command.startswith("tg:") and (command[3:] in ALLOWED_TGS or re.fullmatch(r"[0-9]{1,10}", command[3:])):
        digits = "91" + command[3:] + "#"
        message = f"Anslutningskommando för TG {command[3:]} skickat."
    elif command.startswith("dtmf:"):
        digits = command[5:]
        if not digits or len(digits) > 20 or not re.fullmatch(r"[0-9*#]+", digits):
            return {"ok": False, "error": "DTMF måste innehålla 1–20 tecken: 0–9, * eller #."}, 400
        message = f"DTMF-kommando skickat: {digits}"
    else:
        return {"ok": False, "error": "Otillåtet kommando."}, 400
    try:
        # PTY is a symlink created by SvxLink. Write directly, without a shell.
        with open(DTMF_PTY, "w", encoding="ascii") as pty:
            pty.write(digits + "\n")
            pty.flush()
        return {"ok": True, "message": message}, 200
    except Exception as exc:
        return {"ok": False, "error": f"Kunde inte skriva till {DTMF_PTY}: {exc}"}, 500

def status_data():
    lines = read_log()
    tg, tgtime = latest_match(
        lines, r"(Talker start|Talker stop) on TG #(24075|2407|2406|2405|2404|2403|2402|2401|2400|240|9)(?:[0-9]{3})?:\s*([^\s\x00]+)?",
        lambda m: f"TG {m.group(2)} · {m.group(1)}" + (f" · {m.group(3).strip().split()[0]}" if m.group(3).strip() else "")
    )
    node, nodetime = latest_match(
        lines, r"Node (joined|left):\s*(\S+)",
        lambda m: f"{m.group(2)} · {m.group(1)}"
    )
    current_tg, _ = latest_match(
        lines, r"Selecting TG #([0-9]+)",
        lambda m: m.group(1)
    )
    service, active = service_status()
    errors = sum(1 for line in lines if re.search(r"ERROR|FATAL|CRITICAL", line, re.I))
    warnings = sum(1 for line in lines if re.search(r"WARNING|UDP frame\\(s\\) lost", line, re.I))
    events = sum(1 for line in lines if re.search(r"ReflectorLogic|Talker start|Talker stop|Node joined|Node left|Selecting TG", line, re.I))
    return {"service_status": service, "active": active, "lines": lines,
            "tg_event": tg, "tg_time": tgtime, "node_event": node, "node_time": nodetime,
            "current_tg": current_tg,
            "errors": errors, "warnings": warnings, "events": events}

def reflector_status_data():
    """Fetch and summarize public reflector_proxy JSON without blocking long."""
    req = urllib.request.Request(
        REFLECTOR_PROXY_URL,
        headers={"User-Agent": "SvxLinkWebPanel/3.0", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("Reflektorsvaret är större än 5 MB")
        data = json.loads(raw.decode("utf-8"))
        nodes = data.get("nodes", {}) if isinstance(data, dict) else {}
        if not isinstance(nodes, dict):
            raise ValueError("JSON saknar ett giltigt nodes-objekt")
        by_tg = {}
        visible_count = 0
        for callsign, node in nodes.items():
            if not isinstance(node, dict) or node.get("hidden", False):
                continue
            visible_count += 1
            try:
                tg = int(node.get("tg", 0) or 0)
            except (TypeError, ValueError):
                continue
            if tg <= 0:
                continue
            entry = by_tg.setdefault(tg, {"tg": tg, "nodes": 0, "talkers": 0, "callsigns": []})
            entry["nodes"] += 1
            if node.get("isTalker") is True:
                entry["talkers"] += 1
            entry["callsigns"].append(str(callsign))
        talkgroups = sorted(by_tg.values(), key=lambda item: (-item["talkers"], -item["nodes"], item["tg"]))
        return {"ok": True, "node_count": visible_count, "talkgroups": talkgroups,
                "source": REFLECTOR_PROXY_URL}
    except Exception as exc:
        return {"ok": False, "error": f"Kunde inte hämta reflektorstatus: {exc}", "talkgroups": []}


class Handler(BaseHTTPRequestHandler):
    server_version = "SvxLinkWebPanel"
    sys_version = ""
    def log_message(self, fmt, *args):
        # Avoid writing request details and credentials to stdout.
        pass
    def authorized(self):
        if not PASSWORD:
            self.send_error(503, "Set SVXLINK_WEB_PASSWORD before starting the panel")
            return False
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        got = self.headers.get("Authorization", "")
        if not hmac.compare_digest(got, expected):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SvxLink Control"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True
    def do_GET(self):
        if not self.authorized():
            return
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif path == "/api/status":
            body = json.dumps(status_data(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/reflector":
            body = json.dumps(reflector_status_data(), ensure_ascii=False).encode()
            self.send_response(200 if json.loads(body.decode()).get("ok") else 502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        elif path == "/api/buttons":
            body = json.dumps({"buttons": load_buttons()}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        else:
            body = b"Not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.authorized():
            return
        post_path = urlparse(self.path).path
        if post_path not in ("/api/command", "/api/buttons"):
            self.send_error(404, "Not found")
            return
        # POST requests are browser actions that can transmit DTMF or change controls.
        # Require a same-origin fetch request; browsers include Origin for these POSTs.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not origin or not host:
            self.send_error(403, "Origin and Host headers are required")
            return
        try:
            parsed_origin = urlparse(origin)
            if parsed_origin.scheme not in ("http", "https") or parsed_origin.netloc.lower() != host.lower():
                self.send_error(403, "Cross-origin request rejected")
                return
        except Exception:
            self.send_error(403, "Invalid Origin header")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.send_error(415, "Content-Type must be application/json")
            return
        try:
            length_header = self.headers.get("Content-Length", "")
            if not length_header.isdigit():
                raise ValueError("Ogiltig Content-Length")
            length = int(length_header)
            if length < 1 or length > 1024:
                raise ValueError("Begäran måste vara mellan 1 och 1024 byte")
            raw_body = self.rfile.read(length)
            if len(raw_body) != length:
                raise ValueError("Ofullständig begäran")
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON-roten måste vara ett objekt")
            if post_path == "/api/buttons":
                saved = save_buttons(payload.get("buttons"))
                result, status = {"ok": True, "buttons": saved}, 200
            else:
                action = payload.get("action", "")
                result, status = send_dtmf(action if isinstance(action, str) else "")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            result, status = {"ok": False, "error": str(exc)}, 400
        except Exception:
            # Avoid disclosing local paths, internals, or exception details to clients.
            result, status = {"ok": False, "error": "Internt fel vid behandling av begäran."}, 500
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class HardenedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 20

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(10)
        return request, client_address

if __name__ == "__main__":
    if not PASSWORD:
        raise SystemExit("Set SVXLINK_WEB_PASSWORD in the environment before starting.")
    cert_path = os.environ.get("SVXLINK_WEB_CERT", "").strip()
    key_path = os.environ.get("SVXLINK_WEB_KEY", "").strip()
    if bool(cert_path) != bool(key_path):
        raise SystemExit("Set both SVXLINK_WEB_CERT and SVXLINK_WEB_KEY, or neither.")
    httpd = HardenedThreadingHTTPServer((HOST, PORT), Handler)
    if cert_path and key_path:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        httpd.socket = tls_context.wrap_socket(httpd.socket, server_side=True)
        print(f"SvxLink web panel listening on https://{HOST}:{PORT}")
    else:
        print(f"SvxLink web panel listening on http://{HOST}:{PORT}")
        if HOST not in ("127.0.0.1", "::1", "localhost"):
            print("WARNING: panel is bound to a non-local interface without TLS. Use a reverse proxy with HTTPS or set SVXLINK_WEB_CERT and SVXLINK_WEB_KEY; otherwise restrict access to a trusted VPN/LAN.")
    httpd.serve_forever()
