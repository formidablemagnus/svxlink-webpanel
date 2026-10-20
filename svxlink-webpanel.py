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

HOST = os.environ.get("SVXLINK_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("SVXLINK_WEB_PORT", "8090"))
VERSION = "1.3"
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
<title>SvxLink Control v__VERSION__</title>
<style>
:root{color-scheme:dark;--bg:#09111f;--panel:#111d2e;--panel-raised:#16243a;--line:#293b55;--text:#edf3fb;--muted:#a1b2c8;--green:#59dfa8;--red:#ff8189;--amber:#ffd166;--blue:#83bdff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 50% -25%,#1a2c46 0,transparent 55%),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1180px;margin:auto;padding:28px 24px 36px}header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:1.65rem;letter-spacing:-.03em;margin:0}h2{font-size:1rem;letter-spacing:.01em;margin:0 0 8px}.muted{color:var(--muted);font-size:.88rem}
.header-meta{text-align:right}.status-banner{display:flex;align-items:center;justify-content:space-between;gap:16px;background:linear-gradient(110deg,#142943,#111d2e 70%);border:1px solid #304966;border-radius:14px;padding:15px 18px;margin-bottom:16px}
.status-caption,.k{color:var(--muted);font-size:.78rem;font-weight:650;letter-spacing:.06em;text-transform:uppercase}.status-value{font-weight:750;font-size:1.08rem;margin-top:2px}.status-right{text-align:right}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.card{background:linear-gradient(145deg,var(--panel-raised),var(--panel) 70%);border:1px solid var(--line);border-radius:14px;padding:17px;margin-bottom:14px;box-shadow:0 8px 24px #02081224}
.summary-card{min-height:112px}.summary-card.featured{border-color:#385a7b;background:linear-gradient(145deg,#1b3551,#142238)}.k{letter-spacing:.035em;text-transform:none}.v{font-size:1.2rem;font-weight:720;margin-top:8px;overflow-wrap:anywhere;line-height:1.3}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--amber);margin-right:9px;box-shadow:0 0 12px currentColor}.ok{background:var(--green);color:var(--green)}.bad{background:var(--red);color:var(--red)}
.service-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px}.service-controls button{min-height:36px;padding:6px 11px}.service-result{min-height:1.4em;margin-top:5px}
.toolbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:14px 0 10px}input,select,button{background:#0b1525;border:1px solid var(--line);color:var(--text);border-radius:9px;padding:10px 12px;font:inherit;min-height:42px}
button{cursor:pointer;transition:border-color .15s ease,background-color .15s ease,transform .15s ease}button:hover{border-color:#7399c4;background:#182942}button:active{transform:translateY(1px)}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #83bdff;outline-offset:2px}
input{flex:1;min-width:180px}input::placeholder{color:#8296af}.section-intro{max-width:780px}.tg-buttons{display:flex;flex-wrap:wrap;gap:9px;margin-top:16px}.tg-button{display:inline-flex;align-items:center;gap:7px}.tg-button[aria-pressed="true"]{border-color:var(--blue);background:#173251}.tg-button.tx{border-color:#4f987f}
.badge{display:inline-flex;align-items:center;border-radius:99px;padding:2px 7px;font-size:.72rem;font-weight:750;letter-spacing:.03em;background:#20344d;color:#c9d8e9}.badge.tx{background:#174733;color:#a6f1cc}.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.danger-area{border-top:1px solid var(--line);padding-top:14px;margin-top:17px}.danger-button{border-color:#9b4d59;color:#ff9da3;background:#2a1821}.danger-button:hover{border-color:var(--red);background:#3a1e28}
.admin-panel{background:#0c1727;border:1px solid var(--line);border-radius:11px;padding:16px;margin:14px 0}.admin-row{align-items:center}.status-message{min-height:1.4em;margin-top:10px}
.admin-row.drag-over{border-radius:9px;outline:2px dashed var(--blue);outline-offset:3px}.drag-handle{cursor:grab;touch-action:none;min-width:42px;padding:8px}.drag-handle:active{cursor:grabbing}.drag-handle:focus-visible{outline:3px solid #83bdff;outline-offset:2px}
.log-toolbar{display:grid;grid-template-columns:minmax(180px,1fr) minmax(155px,.45fr) auto;align-items:center;gap:9px;margin:14px 0 9px}.log-toolbar input{width:100%}.log-count{margin-left:auto}
.log{height:55vh;min-height:300px;overflow:auto;background:#080e18;border:1px solid var(--line);border-radius:10px;padding:13px;font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
.logline{padding:3px 7px;border-bottom:1px solid #152033;border-left:2px solid transparent}.logline.warn{color:var(--amber);border-left-color:var(--amber)}.logline.err{color:var(--red);border-left-color:var(--red)}.logline.event{color:var(--blue);border-left-color:var(--blue)}
footer{color:var(--muted);font-size:.8rem;margin-top:12px}
@media(max-width:800px){main{padding:20px 16px 28px}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.log-toolbar{grid-template-columns:1fr 1fr}.log-toolbar input{grid-column:1/-1}.log-toolbar .log-count{grid-column:1/-1;margin:0}}
@media(max-width:520px){main{padding:14px 12px 24px}h1{font-size:1.45rem}.header-meta{text-align:left}.status-banner{align-items:flex-start;flex-direction:column;gap:10px}.status-right{text-align:left}.grid{grid-template-columns:1fr 1fr;gap:8px}.card{padding:14px;margin-bottom:10px}.summary-card{min-height:104px}.v{font-size:1.08rem}.toolbar{gap:7px}.toolbar>*{min-height:44px}.toolbar input{min-width:100%;width:100%}.log-toolbar{grid-template-columns:1fr}.log-toolbar input{grid-column:auto}.log-toolbar .log-count{grid-column:auto}.log{height:50vh;min-height:260px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important}}
</style></head><body><main>
<header><div><h1>SvxLink Control <span class="muted">v__VERSION__</span></h1><div class="muted">Status, händelser och loggar</div></div><div class="header-meta muted">Uppdateras automatiskt<br>Var 5:e sekund</div></header>
<section class="status-banner" aria-label="Tjänstestatus"><div><div class="status-caption">SvxLink-tjänst</div><div class="status-value" id="service"><span class="dot" aria-hidden="true"></span>Hämtar…</div><div class="service-controls"><button id="service-start" type="button" disabled>Starta</button><button id="service-stop" class="danger-button" type="button" disabled>Stoppa</button></div><div class="muted service-result" id="service-result" role="status" aria-live="polite"></div></div><div class="status-right"><div class="status-caption">Senast uppdaterad</div><div class="status-value" id="status-updated">Väntar…</div></div></section>
<div class="grid">
<section class="card summary-card featured"><div class="k">Aktuell vald talgrupp</div><div class="v" id="currenttg">Okänd</div><div class="muted">Baserad på senaste ”Selecting TG” i loggen</div></section>
<section class="card summary-card"><div class="k">Senaste talgruppshändelse</div><div class="v" id="tg">—</div><div class="muted" id="tgtime"></div></section>
<section class="card summary-card"><div class="k">Senaste reflektorhändelse</div><div class="v" id="node">—</div><div class="muted" id="nodetime"></div></section>
<section class="card summary-card"><div class="k">Loggöversikt</div><div class="v" id="counts">—</div><div class="muted">Fel · varningar · händelser</div></section>
</div>
<section class="card"><div class="section-heading"><div><h2>Talgrupper på reflektorn</h2>
<div class="muted section-intro">Överblick över vad noderna lyssnar på just nu. TX visar noder som rapporterar pågående sändning.</div></div>
<div class="badge" id="reflector-nodes">Hämtar…</div></div>
<div class="tg-buttons" id="dynamic-tg-buttons" aria-label="Aktiva talgrupper"><span class="muted">Hämtar reflektorstatus…</span></div>
<div class="muted status-message" id="reflector-updated" role="status" aria-live="polite"></div>
</section>
<section class="card"><h2>Styrning</h2>
<div class="muted">Välj ett snabbval för att skicka ett kommando till SvxLink.</div>
<div class="toolbar" id="tgbuttons" aria-label="Sparade snabbval"></div>
<div class="toolbar"><button id="managebuttons" type="button">Administrera snabbval</button></div>
<div id="buttonadmin" class="admin-panel" style="display:none">
<h2>Administrera DTMF-knappar</h2>
<div class="muted">Lägg till, ändra, ta bort eller dra i handtaget för att ändra ordning på snabbknappar. Använd upp-/nedpilarna när handtaget har fokus. Kommandon skickas direkt när du trycker på knappen.</div>
<div id="buttonrows"></div>
<div class="toolbar"><button id="addbutton" type="button">Lägg till knapp</button><button id="savebuttons" type="button">Spara knappar</button><button id="cancelbuttons" type="button">Stäng</button></div>
<div class="muted status-message" id="adminresult" role="status" aria-live="polite"></div>
</div>
<div class="toolbar">
<input id="customdtmf" maxlength="20" placeholder="Eget DTMF-kommando (0–9, * och #)" aria-label="Eget DTMF-kommando">
<button id="senddtmf" type="button">Skicka DTMF</button>
</div>
<div class="muted">Endast siffrorna 0–9 samt * och # tillåts, högst 20 tecken.</div>
<div class="muted status-message" id="commandresult" role="status" aria-live="polite"></div>
<div class="danger-area"><div class="muted">Frånkoppling lämnar den aktuella reflektorn.</div><div class="toolbar"><button id="disconnect" class="danger-button" type="button">Koppla från reflektorn</button></div></div></section>
<section class="card"><div class="section-heading"><div><h2>Loggar</h2><div class="muted">Senaste händelser från SvxLink-tjänsten</div></div><div class="badge log-count" id="log-count" aria-live="polite">0 träffar</div></div><div class="log-toolbar">
<input id="filter" placeholder="Sök i loggar (t.ex. UDP, ERROR, SA7MHN)" aria-label="Sök i loggar">
<select id="kind" aria-label="Filtrera loggnivå"><option value="all">Alla nivåer</option><option value="error">Fel</option><option value="warn">Varningar</option><option value="event">Reflektor / talgrupp</option></select>
<button id="refresh" type="button">Uppdatera nu</button></div><div class="log" id="logs" role="log" aria-live="polite">Hämtar loggar…</div>
<footer id="updated">Väntar på första uppdateringen.</footer></section>
<footer>Denna panel skickar kommandon till SvxLink men styr inte radion direkt. Loggstatus kan släpa efter faktisk aktivitet.</footer>
</main><script>
const $=id=>document.getElementById(id);
function esc(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
let allLines=[],currentTG='';
let serviceActionPending=false;
function level(s){if(/ERROR|FATAL|CRITICAL/i.test(s))return'err';if(/WARNING|UDP frame\(s\) lost/i.test(s))return'warn';if(/ReflectorLogic|Talker start|Talker stop|Node joined|Node left|Selecting TG/i.test(s))return'event';return'';}
function renderLogs(){const f=$('filter').value.toLowerCase(),k=$('kind').value;
 const filtered=allLines.filter(s=>(!f||s.toLowerCase().includes(f))&&(k==='all'||(k==='error'&&level(s)==='err')||(k==='warn'&&level(s)==='warn')||(k==='event'&&level(s)==='event')));
 $('logs').innerHTML=filtered.slice().reverse().map(s=>'<div class="logline '+level(s)+'">'+esc(s)+'</div>').join('')||'Inga loggrader matchar filtret.';
 $('log-count').textContent=filtered.length+' träff'+(filtered.length===1?'':'ar');
 }
async function update(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json();
 $('service').innerHTML='<span class="dot '+(d.active?'ok':'bad')+'"></span>'+esc(d.service_status);
 $('service-start').disabled=d.active||serviceActionPending;$('service-stop').disabled=!d.active||serviceActionPending;
 $('tg').textContent=d.tg_event||'Ingen identifierad händelse';$('tgtime').textContent=d.tg_time||'';
 currentTG=d.current_tg||'';$('currenttg').textContent=currentTG ? ('TG '+currentTG) : 'Okänd';
 document.querySelectorAll('[data-live-tg]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.liveTg)===String(currentTG)));
 $('node').textContent=d.node_event||'Ingen identifierad händelse';$('nodetime').textContent=d.node_time||'';
 $('counts').textContent=d.errors+' · '+d.warnings+' · '+d.events;
 allLines=d.lines||[];renderLogs();const now=new Date().toLocaleTimeString('sv-SE');$('status-updated').textContent=now;$('updated').textContent='Uppdaterad '+now+' · visar '+allLines.length+' senaste loggrader';
 }catch(e){$('service').innerHTML='<span class="dot bad"></span>Kan inte läsa status';$('status-updated').textContent='Fel';$('updated').textContent='Fel vid uppdatering: '+e.message;}}
async function updateReflector(){
 try{
  const r=await fetch('/api/reflector',{cache:'no-store'});const d=await r.json();
  if(!r.ok||!d.ok)throw Error(d.error||('HTTP '+r.status));
  const groups=d.talkgroups||[];
  $('dynamic-tg-buttons').innerHTML=groups.length?groups.map(tg=>'<button class="tg-button'+(tg.talkers?' tx':'')+'" type="button" data-live-tg="'+esc(String(tg.tg))+'" aria-pressed="'+(String(tg.tg)===String(currentTG))+'" aria-label="Välj talgrupp '+esc(String(tg.tg))+', '+tg.nodes+' noder'+(tg.talkers?', '+tg.talkers+' sänder':'')+'">TG '+esc(String(tg.tg))+'<span class="badge">'+tg.nodes+' nod'+(tg.nodes===1?'':'er')+'</span>'+(tg.talkers?'<span class="badge tx">TX '+tg.talkers+'</span>':'')+'</button>').join(''):'<span class="muted">Inga noder har valt en talgrupp över 0 just nu.</span>';
  $('dynamic-tg-buttons').querySelectorAll('[data-live-tg]').forEach(el=>el.addEventListener('click',()=>sendCommand('tg:'+el.dataset.liveTg)));
  $('reflector-nodes').textContent=d.node_count+' synliga noder';
  $('reflector-updated').textContent='Uppdaterad '+new Date().toLocaleTimeString('sv-SE');
 }catch(e){$('reflector-nodes').textContent='Status saknas';$('dynamic-tg-buttons').innerHTML='<span class="muted">Kunde inte läsa reflektorstatus: '+esc(e.message)+'</span>';}
}
let buttonDefinitions=[];
function renderCommandButtons(){ $('tgbuttons').innerHTML=buttonDefinitions.map((b,i)=>'<button type="button" data-command-index="'+i+'">'+esc(b.label)+'</button>').join('');$('tgbuttons').querySelectorAll('[data-command-index]').forEach(el=>el.addEventListener('click',()=>sendCommand('dtmf:'+buttonDefinitions[Number(el.dataset.commandIndex)].command))); }
function readAdminRows(){return [...$('buttonrows').querySelectorAll('[data-label]')].map(el=>({label:el.value,command:$('buttonrows').querySelector('[data-code="'+el.dataset.label+'"]').value}));}
function moveButton(from,to){
 if(!Number.isInteger(from)||!Number.isInteger(to)||from===to||from<0||to<0||from>=buttonDefinitions.length||to>=buttonDefinitions.length)return;
 const [button]=buttonDefinitions.splice(from,1);buttonDefinitions.splice(to,0,button);
 renderAdmin();$('adminresult').textContent='Knappen flyttades. Spara för att behålla den nya ordningen.';
 $('buttonrows').querySelector('[data-drag-handle="'+to+'"]').focus();
}
function renderAdmin(){
 $('buttonrows').innerHTML=buttonDefinitions.map((b,i)=>'<div class="toolbar admin-row" data-button-index="'+i+'"><button class="drag-handle" type="button" draggable="true" data-drag-handle="'+i+'" aria-label="Flytta knapp '+(i+1)+': '+esc(b.label)+'" title="Dra för att flytta; använd upp-/nedpil för att ändra ordning">⠿</button><input data-label="'+i+'" maxlength="40" placeholder="Knappnamn" aria-label="Knappnamn '+(i+1)+'" value="'+esc(b.label)+'"><input data-code="'+i+'" maxlength="20" placeholder="DTMF" aria-label="DTMF-kommando '+(i+1)+'" value="'+esc(b.command)+'"><button type="button" data-delete="'+i+'">Ta bort</button></div>').join('');
 $('buttonrows').querySelectorAll('[data-delete]').forEach(el=>el.addEventListener('click',()=>{buttonDefinitions=readAdminRows();buttonDefinitions.splice(Number(el.dataset.delete),1);renderAdmin();}));
 $('buttonrows').querySelectorAll('[data-drag-handle]').forEach(handle=>{
  handle.addEventListener('keydown',event=>{
   if(event.key!=='ArrowUp'&&event.key!=='ArrowDown')return;
   event.preventDefault();buttonDefinitions=readAdminRows();
   const from=Number(handle.dataset.dragHandle),to=from+(event.key==='ArrowUp'?-1:1);moveButton(from,to);
  });
  handle.addEventListener('dragstart',event=>{
   buttonDefinitions=readAdminRows();
   event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain','svxlink-button:'+handle.dataset.dragHandle);
  });
 });
 $('buttonrows').querySelectorAll('[data-button-index]').forEach(row=>{
  row.addEventListener('dragover',event=>{event.preventDefault();event.dataTransfer.dropEffect='move';row.classList.add('drag-over');});
  row.addEventListener('dragleave',event=>{if(!row.contains(event.relatedTarget))row.classList.remove('drag-over');});
  row.addEventListener('drop',event=>{
   event.preventDefault();row.classList.remove('drag-over');
   const payload=event.dataTransfer.getData('text/plain'),match=payload.match(/^svxlink-button:(\d+)$/);
   if(!match)return;
   const from=Number(match[1]),to=Number(row.dataset.buttonIndex);
   buttonDefinitions=readAdminRows();moveButton(from,to);
  });
 });
}
async function loadButtons(){try{const r=await fetch('/api/buttons',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||'Kunde inte läsa knappar');buttonDefinitions=d.buttons||[];renderCommandButtons();}catch(e){$('commandresult').textContent='Kunde inte läsa snabbknappar: '+e.message;}}
async function saveButtons(){const next=readAdminRows();try{const r=await fetch('/api/buttons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({buttons:next}),cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'Kunde inte spara');buttonDefinitions=d.buttons;renderCommandButtons();renderAdmin();$('adminresult').textContent='Knapparna sparades.';}catch(e){$('adminresult').textContent='Fel: '+e.message;}}
async function sendCommand(action){const result=$('commandresult');result.textContent='Skickar kommando…';
 try{const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action}),cache:'no-store'});
 const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||('HTTP '+r.status));result.textContent=d.message;
 setTimeout(update,700);
 }catch(e){result.textContent='Kunde inte skicka kommandot: '+e.message;}}
async function controlService(action){
 const result=$('service-result');
 if(action==='stop'&&!window.confirm('Stoppa SvxLink-tjänsten? Pågående trafik och anslutningar bryts.'))return;
 serviceActionPending=true;$('service-start').disabled=true;$('service-stop').disabled=true;result.textContent=action==='start'?'Startar SvxLink…':'Stoppar SvxLink…';
 try{
  const r=await fetch('/api/service',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action}),cache:'no-store'});
   const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||('HTTP '+r.status));result.textContent=d.message;
 }catch(e){result.textContent='Kunde inte styra SvxLink: '+e.message;}
  finally{serviceActionPending=false;setTimeout(update,700);}
}
 $('service-start').addEventListener('click',()=>controlService('start'));
 $('service-stop').addEventListener('click',()=>controlService('stop'));
document.querySelectorAll('[data-tg]').forEach(b=>b.addEventListener('click',()=>sendCommand('tg:'+b.dataset.tg)));
$('disconnect').addEventListener('click',()=>{if(window.confirm('Vill du koppla från den aktuella reflektorn?'))sendCommand('disconnect');});
$('managebuttons').addEventListener('click',()=>{$('buttonadmin').style.display=$('buttonadmin').style.display==='none'?'block':'none';renderAdmin();});
$('addbutton').addEventListener('click',()=>{buttonDefinitions=readAdminRows();buttonDefinitions.push({label:'Ny knapp',command:''});renderAdmin();});
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
PAGE = PAGE.replace("__VERSION__", VERSION)

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

def control_service(action):
    if action not in ("start", "stop"):
        return {"ok": False, "error": "Otillåten tjänståtgärd."}, 400
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", action, SERVICE],
            capture_output=True, text=True, timeout=15, check=False
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Tidsgränsen överskreds när SvxLink skulle styras."}, 504
    except OSError as exc:
        return {"ok": False, "error": f"Kunde inte köra tjänstkommandot: {exc}"}, 500
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        if "a password is required" in details.lower() or "not allowed" in details.lower():
            return {"ok": False, "error": "Panelen saknar behörighet att starta eller stoppa SvxLink. Kontrollera sudoers-konfigurationen."}, 500
        return {"ok": False, "error": f"Kunde inte {('starta' if action == 'start' else 'stoppa')} SvxLink: {details or 'systemctl misslyckades.'}"}, 500
    return {"ok": True, "message": f"SvxLink {('startas' if action == 'start' else 'stoppas')}."}, 200

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
        if post_path not in ("/api/command", "/api/buttons", "/api/service"):
            self.send_error(404, "Not found")
            return
        # POST requests are browser actions that can transmit DTMF or control services.
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
            elif post_path == "/api/service":
                action = payload.get("action", "")
                result, status = control_service(action if isinstance(action, str) else "")
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
