let savedConfig='',savedHash='';
const editor=$('config-content');
function updateSaveState(){
 $('save-config').disabled=editor.disabled||editor.value===savedConfig;
}
async function loadConfig(){
 editor.disabled=true;
 $('save-config').disabled=true;
 $('config-message').textContent='Läser konfigurationen…';
 try{
  const response=await fetch('/api/config',{cache:'no-store'});
  const data=await response.json();
  if(!response.ok||!data.ok)throw Error(data.error||('HTTP '+response.status));
  editor.value=data.content;
  savedConfig=editor.value;
  savedHash=data.sha256;
  editor.disabled=false;
  updateSaveState();
  $('config-message').textContent='Konfigurationen är inläst.';
 }catch(error){
  $('config-message').textContent='Kunde inte läsa konfigurationen: '+error.message;
 }
}
async function saveConfig(){
 if(editor.value===savedConfig)return;
 if(!window.confirm('Spara ändringarna i /etc/svxlink/svxlink.conf? En säkerhetskopia skapas före sparning.'))return;
 const content=editor.value;
 editor.disabled=true;
 $('save-config').disabled=true;
 $('reload-config').disabled=true;
 $('restart-svxlink').disabled=true;
 $('config-message').textContent='Sparar konfigurationen…';
 try{
  const response=await fetch('/api/config',{
   method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({content,sha256:savedHash}),
   cache:'no-store'
  });
  const data=await response.json();
  if(!response.ok||!data.ok)throw Error(data.error||('HTTP '+response.status));
  savedConfig=content;
  savedHash=data.sha256;
  $('config-message').textContent=data.message+' Säkerhetskopia: '+data.backup;
 }catch(error){
  $('config-message').textContent='Kunde inte spara konfigurationen: '+error.message;
 }finally{
  editor.disabled=false;
  $('reload-config').disabled=false;
  $('restart-svxlink').disabled=false;
  updateSaveState();
 }
}
async function restartSvxlink(){
 if(editor.value!==savedConfig){
  $('config-message').textContent='Spara eller läs om osparade ändringar innan du startar om SvxLink.';
  return;
 }
 if(!window.confirm('Starta om SvxLink nu? Pågående trafik och anslutningar bryts.'))return;
 $('restart-svxlink').disabled=true;
 $('config-message').textContent='Startar om SvxLink…';
 try{
  const response=await fetch('/api/service',{
   method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({action:'restart'}),
   cache:'no-store'
  });
  const data=await response.json();
  if(!response.ok||!data.ok)throw Error(data.error||('HTTP '+response.status));
  $('config-message').textContent=data.message;
 }catch(error){
  $('config-message').textContent='Kunde inte starta om SvxLink: '+error.message;
 }finally{
  $('restart-svxlink').disabled=false;
 }
}
editor.addEventListener('input',updateSaveState);
$('reload-config').addEventListener('click',()=>{
 if(editor.value!==savedConfig&&!window.confirm('Osparade ändringar kommer att förloras. Läs in filen på nytt?'))return;
 loadConfig();
});
$('save-config').addEventListener('click',saveConfig);
$('restart-svxlink').addEventListener('click',restartSvxlink);
window.addEventListener('beforeunload',event=>{
 if(editor.value!==savedConfig){event.preventDefault();event.returnValue='';}
});
loadConfig();
