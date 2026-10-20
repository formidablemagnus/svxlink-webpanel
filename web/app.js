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
 $('monitor-tgs').textContent=d.monitor_tgs_error?'Kunde inte läsa konfigurationen':(d.monitor_tgs&&d.monitor_tgs.length?d.monitor_tgs.map(tg=>'TG '+tg).join(' · '):'Inga permanent monitorerade talgrupper');
 $('monitor-tgs-error').textContent=d.monitor_tgs_error||'Läses från [ReflectorLogic] MONITOR_TGS i svxlink.conf';
 document.querySelectorAll('[data-live-tg]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.liveTg)===String(currentTG)));
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
