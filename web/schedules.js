let schedules=[],cronTarget='/dev/shm/dtmf_ctrl';
function currentSchedule(){return {label:$('schedule-label').value.trim(),dtmf:$('schedule-dtmf').value.trim(),minute:$('cron-minute').value.trim(),hour:$('cron-hour').value.trim(),day:$('cron-day').value.trim(),month:$('cron-month').value.trim(),weekday:$('cron-weekday').value.trim()};}
function cronLine(schedule){return schedule.minute+' '+schedule.hour+' '+schedule.day+' '+schedule.month+' '+schedule.weekday+" printf '\\%s\\n' '"+schedule.dtmf+"' > "+cronTarget;}
function updateCronPreview(){ $('cron-preview').textContent=cronLine(currentSchedule()); }
function cronDescription(schedule){
 const weekdays=['söndagar','måndagar','tisdagar','onsdagar','torsdagar','fredagar','lördagar'];
 const months=['januari','februari','mars','april','maj','juni','juli','augusti','september','oktober','november','december'];
 const hour=Number(schedule.hour),minute=Number(schedule.minute);
 const time=Number.isInteger(hour)&&hour>=0&&hour<=23&&Number.isInteger(minute)&&minute>=0&&minute<=59;
 const weekdaysAll=schedule.weekday==='*';
 const dayAll=schedule.day==='*';
 const monthAll=schedule.month==='*';
 const clock=time?' kl. '+String(hour).padStart(2,'0')+':'+String(minute).padStart(2,'0'):'';
 if(time&&dayAll&&monthAll&&/^[0-7]$/.test(schedule.weekday)){
  return weekdays[Number(schedule.weekday)%7]+clock;
 }
 if(time&&dayAll&&weekdaysAll&&monthAll)return 'Varje dag'+clock;
 if(time&&/^\d{1,2}$/.test(schedule.day)&&weekdaysAll&&monthAll){
  const day=Number(schedule.day);
  if(day>=1&&day<=31){
   const suffix=(day%10===1&&day!==11)||(day%10===2&&day!==12)?'a':'e';
   return 'Den '+day+':'+suffix+' varje månad'+clock;
  }
 }
 if(time&&/^\d{1,2}$/.test(schedule.day)&&/^\d{1,2}$/.test(schedule.month)&&weekdaysAll){
  const day=Number(schedule.day),month=Number(schedule.month);
  if(day>=1&&day<=31&&month>=1&&month<=12)return day+' '+months[month-1]+clock;
 }
 return 'Cron-schema: '+[schedule.minute,schedule.hour,schedule.day,schedule.month,schedule.weekday].join(' ');
}
function validCronField(value,min,max){
 if(!value||value.length>20)return false;
 return value.split(',').every(part=>{
  const match=part.match(/^(\*|(\d+)(?:-(\d+))?)(?:\/(\d+))?$/);
  if(!match)return false;
  if(match[4]!==undefined&&Number(match[4])<1)return false;
  if(match[2]===undefined)return true;
  const start=Number(match[2]),end=match[3]===undefined?start:Number(match[3]);
  return start>=min&&end<=max&&start<=end;
 });
}
function renderSchedules(){
 $('schedule-count').textContent=schedules.length+' schemalägg'+(schedules.length===1?'ning':'ningar');
 $('schedule-list').innerHTML=schedules.length?schedules.map((schedule,index)=>'<div class="schedule-row"><div class="schedule-info"><strong>'+esc(schedule.label||schedule.dtmf)+'</strong><div class="schedule-description">'+esc(cronDescription(schedule))+'</div><div class="muted">DTMF: <code>'+esc(schedule.dtmf)+'</code></div></div><div class="schedule-actions"><button type="button" data-edit-schedule="'+index+'">Ändra</button><button type="button" data-remove-schedule="'+index+'">Ta bort</button></div></div>').join(''):'<div class="muted schedule-empty">Inga panelhanterade schemaläggningar.</div>';
 $('schedule-list').querySelectorAll('[data-remove-schedule]').forEach(button=>button.addEventListener('click',()=>{schedules.splice(Number(button.dataset.removeSchedule),1);renderSchedules();}));
 $('schedule-list').querySelectorAll('[data-edit-schedule]').forEach(button=>button.addEventListener('click',()=>{
  const schedule=schedules.splice(Number(button.dataset.editSchedule),1)[0];
  $('schedule-label').value=schedule.label;$('schedule-dtmf').value=schedule.dtmf;
  for(const field of ['minute','hour','day','month','weekday'])$('cron-'+field).value=schedule[field];
  renderSchedules();updateCronPreview();$('schedule-message').textContent='Schemaläggningen har lagts i formuläret. Spara ändringarna när du är klar.';
 }));
}
async function loadSchedules(){
 $('schedule-message').textContent='Läser crontab…';
 try{const r=await fetch('/api/schedules',{cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||('HTTP '+r.status));
  schedules=d.schedules;cronTarget=d.cron_target;$('other-crontab').textContent=d.other_lines.length?d.other_lines.join('\n'):'Inga övriga crontab-rader.';
  updateCronPreview();
  renderSchedules();$('schedule-message').textContent='Crontab läst från användaren svxlink.';
 }catch(e){$('schedule-count').textContent='Inte tillgänglig';$('schedule-message').textContent='Kunde inte läsa crontab: '+e.message;}
}
async function saveSchedules(){
 $('schedule-message').textContent='Sparar schemaläggningar…';
 const clean=schedules.map(({cron_line,...schedule})=>schedule);
 try{const r=await fetch('/api/schedules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({schedules:clean}),cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||('HTTP '+r.status));
  schedules=d.schedules;renderSchedules();$('other-crontab').textContent=d.other_lines.length?d.other_lines.join('\n'):'Inga övriga crontab-rader.';$('schedule-message').textContent=d.message;
 }catch(e){$('schedule-message').textContent='Kunde inte spara crontab: '+e.message;}
}
document.querySelectorAll('#cron-minute,#cron-hour,#cron-day,#cron-month,#cron-weekday,#schedule-dtmf,#schedule-label').forEach(input=>input.addEventListener('input',updateCronPreview));
$('add-schedule').addEventListener('click',()=>{
 const schedule=currentSchedule();
 const ranges=[['minute',0,59],['hour',0,23],['day',1,31],['month',1,12],['weekday',0,7]];
 const invalid=ranges.find(([field,min,max])=>!validCronField(schedule[field],min,max));
 if(invalid){$('schedule-message').textContent='Ogiltigt cron-fält: '+invalid[0]+'. Kontrollera värde, intervall eller steg.';return;}
 if(!schedule.dtmf||schedule.dtmf.length>20||!/^[0-9*#]+$/.test(schedule.dtmf)){$('schedule-message').textContent='DTMF-koden måste innehålla 1–20 tecken: 0–9, * eller #.';return;}
 if(schedules.length>=100){$('schedule-message').textContent='Du kan ha högst 100 schemaläggningar i panelen.';return;}
 schedules.push(schedule);renderSchedules();$('schedule-message').textContent='Schemaläggningen har lagts till. Spara för att skriva den till crontab.';
});
$('save-schedules').addEventListener('click',saveSchedules);
$('reload-schedules').addEventListener('click',loadSchedules);
loadSchedules();updateCronPreview();
