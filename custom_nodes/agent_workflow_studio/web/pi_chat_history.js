// Private ComfyUI userdata, not agent-editable workspace files or Pi resources.
const BASE='pi-chat-history';
const ID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// ponytail: 8 MiB per text archive; report overflow, never silently trim history.
const MAX_BYTES=8*1024*1024;

async function directory(workflow) {
  if(typeof workflow!=='string'||!workflow||workflow.length>512)throw Error('Load a workflow before viewing chat history.');
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(workflow));
  return `${BASE}/${Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('')}`;
}
function validate(entry,workflow,id=entry?.id) {
  if(!entry||entry.version!==1||!ID.test(id)||entry.id!==id||entry.workflow_id!==workflow||typeof entry.title!=='string'||entry.title.length>160||!Number.isFinite(entry.started)||!Number.isFinite(entry.updated)||!Array.isArray(entry.messages)||!entry.messages.every(message=>message&&['user','assistant','notice'].includes(message.role)&&typeof message.text==='string'&&(message.incomplete===undefined||typeof message.incomplete==='boolean')&&Object.keys(message).every(key=>['role','text','incomplete'].includes(key))))throw Error('Invalid chat history record.');
  return entry;
}
export async function saveChatHistory(api,entry) {
  validate(entry,entry.workflow_id);const text=JSON.stringify(entry);
  if(new TextEncoder().encode(text).length>MAX_BYTES)throw Error('Chat history exceeds 8 MiB. Copy this conversation before leaving; existing history is preserved.');
  const path=`${await directory(entry.workflow_id)}/${entry.id}.json`;
  await api.storeUserData(path,text,{overwrite:true,stringify:false,throwOnError:true,keepalive:new TextEncoder().encode(text).length<48*1024});
}
export async function readChatHistory(api,workflow,id) {
  if(!ID.test(id))throw Error('Invalid chat history ID.');
  const response=await api.getUserData(`${await directory(workflow)}/${id}.json`);
  if(!response.ok)throw Error(`Cannot read chat history (${response.status}).`);
  const text=await response.text();
  if(new TextEncoder().encode(text).length>MAX_BYTES)throw Error('Chat history exceeds the 8 MiB viewer limit.');
  return validate(JSON.parse(text),workflow,id);
}
export async function listChatHistory(api,workflow) {
  const prefix=`${await directory(workflow)}/`,files=await api.listUserDataFullInfo(prefix),sessions=[],errors=[];
  // ponytail: read each archive for its title; add a metadata index if listing grows slow.
  for(const file of files){
    if(typeof file.path!=='string'||!file.path.endsWith('.json'))continue;
    const id=file.path.slice(0,-5);if(!ID.test(id))continue;
    try{if(file.size>MAX_BYTES)throw Error('Chat archive exceeds 8 MiB.');const entry=await readChatHistory(api,workflow,id);sessions.push({id:entry.id,title:entry.title,started:entry.started,updated:entry.updated});}
    catch{errors.push(id);}
  }
  return {sessions:sessions.sort((a,b)=>b.updated-a.updated),errors};
}
