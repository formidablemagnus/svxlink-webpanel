const editor=$('node-info-content');
let savedContent='',savedHash='';
function updateSaveState(){
 $('save-node-info').disabled=editor.disabled||editor.value===savedContent;
}
async function loadNodeInfo(){
 editor.disabled=true;
 $('save-node-info').disabled=true;
 $('reload-node-info').disabled=true;
 $('node-info-message').textContent='Läser node_info.json…';
 try{
  const response=await fetch('/api/node-info',{cache:'no-store'});
  const data=await response.json();
  if(!response.ok||!data.ok)throw Error(data.error||('HTTP '+response.status));
  editor.value=data.content;
  savedContent=editor.value;
  savedHash=data.sha256;
  editor.disabled=false;
  updateSaveState();
  $('node-info-message').textContent='node_info.json är inläst.';
 }catch(error){
  $('node-info-message').textContent='Kunde inte läsa node_info.json: '+error.message;
 }finally{
  $('reload-node-info').disabled=false;
 }
}
async function saveNodeInfo(){
 if(editor.value===savedContent)return;
 try{
  JSON.parse(editor.value);
 }catch(error){
  $('node-info-message').textContent='Ogiltig JSON: '+error.message;
  return;
 }
 if(!window.confirm('Spara ändringarna i /etc/svxlink/node_info.json? En säkerhetskopia skapas före sparning.'))return;
 const content=editor.value;
 editor.disabled=true;
 $('save-node-info').disabled=true;
 $('reload-node-info').disabled=true;
 $('node-info-message').textContent='Sparar node_info.json…';
 try{
  const response=await fetch('/api/node-info',{
   method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({content,sha256:savedHash}),
   cache:'no-store'
  });
  const data=await response.json();
  if(!response.ok||!data.ok)throw Error(data.error||('HTTP '+response.status));
  savedContent=content;
  savedHash=data.sha256;
  $('node-info-message').textContent=data.message+' Säkerhetskopia: '+data.backup;
 }catch(error){
  $('node-info-message').textContent='Kunde inte spara node_info.json: '+error.message;
 }finally{
  editor.disabled=false;
  $('reload-node-info').disabled=false;
  updateSaveState();
 }
}
editor.addEventListener('input',updateSaveState);
$('reload-node-info').addEventListener('click',()=>{
 if(editor.value!==savedContent&&!window.confirm('Osparade ändringar kommer att förloras. Läs in filen på nytt?'))return;
 loadNodeInfo();
});
$('save-node-info').addEventListener('click',saveNodeInfo);
window.addEventListener('beforeunload',event=>{
 if(editor.value!==savedContent){event.preventDefault();event.returnValue='';}
});
loadNodeInfo();
