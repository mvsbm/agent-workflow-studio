import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {openPiSettings} from './pi_settings.js';
import {createCanvasBridge} from './pi_canvas.js';
import {saveChatHistory,listChatHistory,readChatHistory} from './pi_chat_history.js';
import {commitWorkflow} from './project_versions.js';
import {toolPhrase,finishedText} from './chat_words.js';
import {uploadFolder,workflowFolder} from './data_folder.js';

const host=app?.extensionManager?.sidebarTab?.registerSidebarTab ? app.extensionManager.sidebarTab : app?.extensionManager;
const element=(tag,text,props={})=>Object.assign(document.createElement(tag),{textContent:text??'',...props});
const CLAUDE_MODELS=[['default','Claude Code default'],['sonnet','Sonnet'],['opus','Opus'],['haiku','Haiku']],EFFORTS=['default','low','medium','high','xhigh','max'],PI_THINKING=['off','minimal','low','medium','high','xhigh','max'];
const configLabel=config=>!config?'Pi defaults · click to choose agent / model':config.agent==='claude_code'?['Claude Code',config.model??'default',config.effort&&config.effort!=='default'?`effort ${config.effort}`:''].filter(Boolean).join(' · '):[config.provider,config.model,config.credential_source].filter(Boolean).join(' · ');
const textOf=message=>(message?.content??[]).filter(block=>block.type==='text').map(block=>block.text).join('\n');

function createChat() {
  const root=element('section',null,{className:'aw-pi-chat'});
  const header=element('header'),heading=element('h2','Agent'),model=element('summary',configLabel(null),{className:'aw-pi-model'});model.setAttribute('aria-label','Agent and model');
  const modelPicker=element('details',null,{className:'aw-pi-model-picker'}),configForm=element('form',null,{className:'aw-pi-config'});modelPicker.append(model,configForm);
  const agentSelect=element('select',null,{ariaLabel:'Chat agent'}),providerSelect=element('select',null,{ariaLabel:'Pi provider'}),modelSelect=element('select',null,{ariaLabel:'Pi model'}),thinkingSelect=element('select',null,{ariaLabel:'Pi thinking level'}),authSelect=element('select',null,{ariaLabel:'Pi credential source'}),saveModel=element('button','Apply',{type:'submit',className:'p-button p-component'}),configNote=element('small','Remembered per workflow on this browser.');
  const rows={};for(const [label,field] of [['Agent',agentSelect],['Provider',providerSelect],['Model',modelSelect],['Thinking',thinkingSelect],['Credentials',authSelect]]){const row=element('label',label);row.append(field);configForm.append(row);rows[label]=row;}for(const [value,label] of [['pi','Pi (any provider)'],['claude_code','Claude Code (your Claude login)']])agentSelect.add(new Option(label,value));for(const [value,label] of [['pi_managed','Pi managed'],['studio_keychain','Studio Keychain']])authSelect.add(new Option(label,value));const closeModel=element('button','Close',{type:'button',className:'p-button p-component',onclick:()=>{modelPicker.open=false;model.focus();}});configForm.append(saveModel,closeModel,configNote);configForm.addEventListener('keydown',event=>{if(event.key==='Escape'){modelPicker.open=false;model.focus();}});
  const autoApprove=element('input',null,{type:'checkbox'}),runOption=element('label','Auto-approve workflow runs',{className:'aw-pi-run-option'});autoApprove.setAttribute('aria-label','Auto-approve workflow runs');runOption.prepend(autoApprove);runOption.title='For this workflow only. Native runs skip confirmation; other permission and execution safeguards remain.';
  const addons=element('p','Extensions load on connect',{className:'aw-pi-model'});addons.setAttribute('aria-label','Pi extensions');const skills=element('small','ComfyUI skills load on connect',{className:'aw-pi-addons'});skills.setAttribute('aria-label','ComfyUI skills');
  const workspace=element('p','Files bind to the workflow on connect',{className:'aw-pi-model'});workspace.setAttribute('aria-label','Workflow workspace');
  const controls=element('div',null,{className:'aw-pi-controls'});
  const button=(label,action,icon)=>{const b=element('button',null,{type:'button',className:'p-button p-component p-button-text p-button-sm',title:label});b.setAttribute('aria-label',label);if(icon)b.append(element('i',null,{className:`pi ${icon}`}));b.append(element('span',label));b.addEventListener('click',action);return b;};
  // The connection in one line; what it is made of (agent, model, add-ons, skills, files) under it.
  const connection=element('details',null,{className:'aw-pi-connection'}),connectionLabel=element('summary'),connectionText=element('span','Not connected'),notices=element('div',null,{className:'aw-pi-notices'});connectionLabel.append(connectionText,element('i',null,{className:'pi pi-chevron-down'}));connectionLabel.title='The agent, its model and add-ons, credentials and run approval';connection.append(connectionLabel);
  const status=element('p','',{className:'aw-pi-status'});status.setAttribute('role','status');
  const log=element('div',null,{className:'aw-pi-messages'});log.setAttribute('role','log');log.setAttribute('aria-label','Pi conversation');
  const empty=element('div',null,{className:'aw-pi-empty'});empty.append(element('h3','Build a workflow by describing it'),element('p','Say what data you have and what you need from it, in your own words. The agent asks a few questions, shows you a short contract and plan to approve, then builds the workflow on the canvas and checks every step. Nothing runs without your OK.'));
  const suggestions=element('div',null,{className:'aw-pi-suggestions'});
  const form=element('form',null,{className:'aw-pi-composer'}),input=element('textarea',null,{rows:3,placeholder:'Describe what you want to build, or ask about this canvas…',maxLength:16000});input.setAttribute('aria-label','Message the agent');
  const actions=element('div',null,{className:'aw-pi-controls'}),sendButton=button('Send',()=>form.requestSubmit(),'pi-send'),stopButton=button('Stop',()=>{stopped=true;settleDialogs(true);if(socket?.readyState===WebSocket.OPEN)send({type:'abort'});status.textContent='Stopping Pi…';},'pi-stop');stopButton.hidden=true;stopButton.title='Stop Pi; use native Interrupt to stop an already queued workflow.';
  const hint=element('small','Ctrl/⌘ + Enter to send');
  const archiveNote=element('small','',{className:'aw-pi-history-status',hidden:true});archiveNote.setAttribute('role','status');
  let agentName='Pi',socket,ready=false,busy=false,connecting=false,configuring=false,resetting=false,activeConfig,modelCatalog=[],stopped=false,pendingText,assistant,toolRows=new Map(),dialogs=new Map(),generation=0;
  let filesSupported=false,turnSubmitted=false,turnCompleted=false,turnFailed=false,turnWrites=false;
  const files=element('details',null,{className:'aw-pi-files'}),filesSummary=element('summary','Workflow files'),filesList=element('div'),filesNote=element('small','Workspace files are not native node execution outputs.'),refreshFiles=button('Refresh files',()=>requestFiles());files.append(filesSummary,filesNote,refreshFiles,filesList);files.hidden=true;
  function requestFiles() {try{checkWorkflow();if(ready&&filesSupported)send({type:'workspace_files'});else if(!connecting&&!ready)connect(null);}catch(error){filesNote.textContent=error.message;}}
  files.addEventListener('toggle',()=>{if(files.open)requestFiles();});
  function showFiles(result) {
    filesSummary.textContent=`Workflow files (${result.files.length})`;files.hidden=!result.files.length;filesList.replaceChildren();filesNote.textContent='Workspace files are not native node execution outputs.';
    for(const file of result.files){const item=element('div',null,{className:'aw-pi-file'}),download=button('Download',()=>{try{checkWorkflow();send({type:'workspace_file',path:file.path});}catch(error){filesNote.textContent=error.message;}});download.setAttribute('aria-label',`Download ${file.path}`);item.append(element('span',`${file.path} · ${file.size} bytes`),download);filesList.append(item);}
    if(!result.files.length)filesList.append(element('p','No saved workspace files.'));
  }
  function downloadFile(result) {checkWorkflow();const bytes=Uint8Array.from(atob(result.data),c=>c.charCodeAt(0)),url=URL.createObjectURL(new Blob([bytes],{type:'application/octet-stream'})),link=element('a',null,{href:url,download:result.path.split('/').at(-1)});root.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function executionSummary() {return turnFailed?'Native workflow failed; inspect execution errors.':turnCompleted?'Native node execution succeeded; inspect outputs for usefulness.':turnSubmitted?'Native workflow submitted; completion has not been verified.':`No native workflow was submitted by ${agentName} this turn.`;}

  const nativeOutputs=new Map();
  function showNativeFiles(result) {
    if(!result.prompt_id||result.status?.completed!==true||result.status?.status_str!=='success')return;
    checkWorkflow();const links=[];
    for(const output of Object.values(result.outputs??{}))for(const file of Array.isArray(output?.files)?output.files:[]){
      if(!file||typeof file!=='object'||!['output','temp'].includes(file.type)||typeof file.filename!=='string'||/[\\/\\\\\x00]/.test(file.filename)||typeof file.subfolder!=='string'||file.subfolder.split(/[\\/\\\\]/).some(part=>part==='..'||part==='.')||file.subfolder.includes('\x00'))continue;
      // ponytail: show at most 64 native artifact links; add paging if needed.
      if(links.length>=64)break;
      const params=new URLSearchParams({filename:file.filename,subfolder:file.subfolder,type:file.type});links.push(element('a',`Download ${file.filename}`,{href:api.apiURL(`/view?${params}`),download:file.filename,className:'p-button p-component'}));
    }
    if(!links.length)return;
    let card=nativeOutputs.get(result.prompt_id);if(!card){card=element('section',null,{className:'aw-pi-tool aw-pi-native-files'});card.setAttribute('aria-label','Native workflow output files');nativeOutputs.set(result.prompt_id,card);log.append(card);}
    card.replaceChildren(element('strong','Native workflow output files'),element('small',`Prompt ${result.prompt_id}`),...links);scroll();
  }
  let archive=null,archiveAssistant=null,archiveTimer,archiveSave=Promise.resolve(),archiveError=null;
  const historyDialog=element('dialog',null,{className:'aw-pi-history-dialog'}),historySelect=element('select'),historyNote=element('p','Read-only saved messages and execution status. This does not resume Pi or alter the canvas.'),historyLog=element('div',null,{className:'aw-pi-history-messages'});historyDialog.setAttribute('aria-label','Pi chat history');historySelect.setAttribute('aria-label','Saved Pi chats');historyLog.setAttribute('aria-label','Historical Pi conversation');
  let historyWorkflow;
  function saveArchive() {
    clearTimeout(archiveTimer);if(!archive)return archiveSave;
    archive.updated=Date.now();const snapshot=structuredClone(archive);
    archiveSave=archiveSave.catch(()=>{}).then(()=>saveChatHistory(api,snapshot)).then(()=>{archiveError=null;archiveNote.hidden=true;}).catch(error=>{archiveError=error;archiveNote.hidden=false;archiveNote.textContent=`History not saved: ${error.message} Copy this conversation before leaving.`;});return archiveSave;
  }
  function archiveMessage(role,text) {
    if(!text||(role==='user'&&text.startsWith('/')))return;
    if(!archive){if(role!=='user')return;archive={version:1,id:crypto.randomUUID(),workflow_id:boundWorkflow,title:text.trim().replace(/\s+/g,' ').slice(0,160),started:Date.now(),updated:Date.now(),messages:[]};}
    archive.messages.push({role,text});saveArchive();
  }
  function archiveReply(text,complete,incomplete=false) {
    if(!archive||(!text&&!archiveAssistant))return;
    if(!archiveAssistant){archiveAssistant={role:'assistant',text:'',incomplete:true};archive.messages.push(archiveAssistant);}
    archiveAssistant.text=complete?(text||archiveAssistant.text):archiveAssistant.text+text;archiveAssistant.incomplete=!complete||incomplete;
    // ponytail: checkpoint partial public text each second; final messages flush immediately.
    if(complete)saveArchive();else{clearTimeout(archiveTimer);archiveTimer=setTimeout(saveArchive,1000);}
  }
  async function showHistory(id) {
    historyLog.replaceChildren();if(!id)return;
    try{const entry=await readChatHistory(api,historyWorkflow,id);if(!historyDialog.open||historySelect.value!==id)return;for(const message of entry.messages){const card=element('article',null,{className:`aw-pi-message aw-pi-${message.role}`}),body=element('div',null,{className:'aw-pi-message-text'});card.append(element('h3',`${message.role==='user'?'You':message.role==='assistant'?'Pi':'Execution status'}${message.incomplete?' · incomplete':''}`));if(message.role==='assistant'&&app.extensionManager.renderMarkdownToHtml)body.innerHTML=app.extensionManager.renderMarkdownToHtml(message.text);else body.textContent=message.text;card.append(body);historyLog.append(card);}}
    catch(error){historyNote.textContent=error.message;}
  }
  async function openHistory() {
    historyWorkflow=workflowId();historySelect.replaceChildren();historySelect.disabled=true;historyLog.replaceChildren();historyNote.textContent='Loading saved chats for this workflow…';historyDialog.showModal();
    try{await saveArchive();const workflow=historyWorkflow,{sessions,errors}=await listChatHistory(api,workflow);if(!historyDialog.open||workflow!==historyWorkflow)return;historyNote.textContent=sessions.length?'Read-only public messages and execution status; no tool payloads/private reasoning. Close to return to the unchanged live chat.':'No saved chats for this workflow. Earlier unsaved sessions cannot be recovered.';if(errors.length)historyNote.textContent+=` ${errors.length} unreadable archives were left untouched.`;for(const session of sessions)historySelect.add(new Option(`${new Date(session.started).toLocaleString()} · ${session.title}`,session.id));historySelect.disabled=!sessions.length;if(sessions.length)await showHistory(historySelect.value);}
    catch(error){historyNote.textContent=`Cannot load chat history: ${error.message}`;}
  }
  historySelect.addEventListener('change',()=>showHistory(historySelect.value));const closeHistory=button('Close history',()=>historyDialog.close());historyDialog.append(element('h2','Chat history · current workflow'),closeHistory,historyNote,historySelect,historyLog);
  let boundWorkflow=null;
  const workflowId=()=> (app.rootGraph??app.graph?.rootGraph??app.graph)?.id;
  function checkWorkflow() {if(boundWorkflow&&workflowId()!==boundWorkflow)throw Error('Workflow changed. Start New chat to bind its workspace; existing files and canvas are preserved.');}
  function showAddons(value) {
    if(!value||typeof value!=='object')return;
    const sol=value.sol_pi,pony=value.ponytail,parts=[],details=[];
    if(sol){parts.push('SoL-Pi');details.push(`SoL-Pi ${sol.version}: `+['actionFusion','observationPack','evidencePreservingReducer','onlineContextCompact'].map(key=>`${key}: ${sol[key]?'on':'off'}`).join(', '));}
    if(pony){parts.push(`Ponytail ${String(pony.mode).toUpperCase()}`);details.push(`Ponytail ${pony.version}`);}
    addons.textContent=parts.length?parts.join(' · '):agentName==='Claude'?'Claude Code · studio tools only (no shell, no host files, no personal settings)':'No Pi add-ons';
    addons.title=details.length?details.join(' · '):'SoL-Pi and Ponytail are optional and not installed in this Pi profile.';
  }
  function showConnection() {
    let config=activeConfig;try{config??=requestedConfig();}catch{}
    const name=config?.agent==='claude_code'?['Claude Code',config.model!=='default'&&config.model].filter(Boolean).join(' · '):config?.model??'Pi defaults';
    connectionText.textContent=`${ready?'Connected':'Not connected'} · ${name}`;connectionLabel.dataset.state=ready?'on':'off';
  }
  function scroll() {log.scrollTop=log.scrollHeight;}
  function row(role,text) {empty.remove();const article=element('article',null,{className:`aw-pi-message aw-pi-${role}`});article.append(element('h3',role==='user'?'You':role==='assistant'?agentName:'Notice'));const body=element('div',text,{className:'aw-pi-message-text'});article.append(body);log.append(article);scroll();return body;}
  function update() {sendButton.disabled=busy||connecting||configuring||resetting||!input.value.trim();input.disabled=connecting||configuring||resetting;stopButton.hidden=!busy;saveModel.disabled=busy||connecting||configuring;for(const field of [agentSelect,providerSelect,modelSelect,thinkingSelect,authSelect])field.disabled=busy||connecting||configuring;}
  function send(record) {const data=JSON.stringify(record);if(new Blob([data]).size>1048576)throw Error('Canvas response exceeds the 1 MiB chat limit. Inspect fewer node schemas at a time.');socket.send(data);}
  function settleDialogs(notify=false) {for(const d of dialogs.values()){d.cancel(notify);d.card.remove();}dialogs.clear();}
  function permission(request,respond) {
    const card=element('section',null,{className:'aw-pi-permission'});card.setAttribute('role','group');card.setAttribute('aria-label',request.title??'Pi permission');
    card.append(element('h3',request.title??'Pi permission'));
    if(request.summary){// the few lines to decide on; everything else one click away
      const full=element('details',null,{className:'aw-pi-full'}),label=/contract/i.test(request.title)?'Full contract':/design/i.test(request.title)?'Full design':/tool/i.test(request.title)?'Full tool, with its code':'Details';
      full.append(element('summary',label),element('p',request.message));card.append(element('p',request.summary,{className:'aw-pi-summary'}),full);
    }else if(request.message)card.append(element('p',request.message));
    let editor;
    if(['input','editor'].includes(request.method)){editor=element(request.method==='editor'?'textarea':'input',null,{value:request.prefill??'',placeholder:request.placeholder??''});editor.setAttribute('aria-label',request.title??'Pi response');card.append(editor);}
    const buttons=element('div',null,{className:'aw-pi-controls'});let timeout;
    const finish=value=>{clearTimeout(timeout);dialogs.delete(request.id);card.remove();respond(value);};
    if(request.method==='select')for(const option of request.options??[])buttons.append(button(option,()=>finish({value:option})));
    else buttons.append(button(request.method==='confirm'?'Allow':'Submit',()=>finish(request.method==='confirm'?{confirmed:true}:{value:editor.value})));
    buttons.append(button(request.method==='confirm'?'Deny':'Cancel',()=>finish({cancelled:true})));
    card.append(buttons);log.append(card);scroll();
    dialogs.set(request.id,{card,cancel:(notify)=>{clearTimeout(timeout);respond({cancelled:true},notify);}});
    if(request.timeout)timeout=setTimeout(()=>finish({cancelled:true}),request.timeout);
  }
  const confirm=(title,message,summary)=>new Promise(resolve=>permission({id:crypto.randomUUID(),method:'confirm',title,message,summary},response=>resolve(response.confirmed===true)));
  const bridge=createCanvasBridge(app,api,{confirm:async(...args)=>{checkWorkflow();const approved=await confirm(...args);checkWorkflow();return approved;},autoApproveRun:()=>{checkWorkflow();return preferences().autoApproveRuns===true;}});

  function configuration() {
    const selected=Object.values(app.canvas.selected_nodes??{}).filter(n=>n.type==='AgentWorkflowPiModelConfig');
    if(selected.length>1)throw Error('Select only one Pi Model Config node.');
    const configs=(app.graph.nodes??app.graph._nodes??[]).filter(n=>n.type==='AgentWorkflowPiModelConfig');
    const node=selected[0]??(configs.length===1?configs[0]:null);
    if(!node)return null;
    const value={version:1};for(const name of ['provider','model','thinking_level','credential_source'])value[name]=node.widgets?.find(w=>w.name===name)?.value;
    if(!value.provider||!value.model)throw Error('Choose provider/model in the Pi Model Config node, or remove it to use Pi defaults.');
    return value;
  }
  const preferenceKey=()=>{const id=workflowId();if(typeof id!=='string'||!id)throw Error('Load a native workflow first.');return `agent-workflow-studio:pi-chat:${id}`;};
  function preferences(){try{const value=JSON.parse(localStorage.getItem(preferenceKey())??'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}catch{return {};}}
  function remember(change){localStorage.setItem(preferenceKey(),JSON.stringify({...preferences(),...change}));}
  const requestedConfig=()=>preferences().pi_config??configuration();
  function refreshRunOption(){autoApprove.checked=preferences().autoApproveRuns===true;}
  autoApprove.addEventListener('change',()=>{try{remember({autoApproveRuns:autoApprove.checked});}catch(error){refreshRunOption();row('notice',`Could not remember run approval: ${error.message}`);}});
  function fillModels(selected){modelSelect.replaceChildren();for(const item of modelCatalog.filter(item=>item.provider===providerSelect.value))modelSelect.add(new Option(item.name??item.id,item.id));if(selected&&!Array.from(modelSelect.options).some(option=>option.value===selected))modelSelect.add(new Option(selected,selected));if(selected)modelSelect.value=selected;}
  let pickerView=0;// a later choice wins over a slow earlier load
  const showRow=(label,shown)=>{rows[label].hidden=!shown;};
  const fillThinking=(values,selected,label)=>{thinkingSelect.replaceChildren();for(const value of values)thinkingSelect.add(new Option(value,value));thinkingSelect.value=values.includes(selected)?selected:values[0];rows.Thinking.firstChild.textContent=label;};
  async function showClaude(desired){const view=++pickerView;showRow('Provider',false);showRow('Credentials',false);modelSelect.replaceChildren();for(const [value,label] of CLAUDE_MODELS)modelSelect.add(new Option(label,value));if(desired?.model&&!CLAUDE_MODELS.some(([value])=>value===desired.model))modelSelect.add(new Option(desired.model,desired.model));modelSelect.value=desired?.model??'default';fillThinking(EFFORTS,desired?.effort,'Effort');configNote.textContent='Checking Claude Code…';try{const response=await api.fetchApi('/agent-workflow-studio/claude/status'),login=await response.json();if(view!==pickerView)return;configNote.textContent=!login.installed?'Claude Code is not installed on this computer (the claude command was not found).':!login.logged_in?'Claude Code is installed but not logged in: run claude once in a terminal and log in.':`Claude Code · logged in (${login.method}). Uses only the studio's tools: no shell, no host files, none of your Claude Code settings. Remembered per workflow.`;}catch(error){configNote.textContent=error.message;}}
  agentSelect.addEventListener('change',()=>{if(agentSelect.value==='claude_code')showClaude(null);else loadPi(null);update();});
  modelPicker.addEventListener('toggle',async()=>{if(!modelPicker.open)return;refreshRunOption();const desired=requestedConfig()??(boundWorkflow===workflowId()?activeConfig:null);agentSelect.value=desired?.agent==='claude_code'?'claude_code':'pi';if(agentSelect.value==='claude_code')await showClaude(desired);else await loadPi(desired);update();});
  async function loadPi(desired){const view=++pickerView;showRow('Provider',true);showRow('Credentials',true);fillThinking(PI_THINKING,desired?.thinking_level,'Thinking');configNote.textContent='Loading local Pi model catalogue…';try{const response=await api.fetchApi('/agent-workflow-studio/pi/catalog');const data=await response.json();if(view!==pickerView)return;if(!response.ok)throw Error(data.error??'Could not load Pi models.');modelCatalog=data.models??[];providerSelect.replaceChildren();const providers=new Set(modelCatalog.map(item=>item.provider));if(desired?.provider)providers.add(desired.provider);for(const provider of providers)providerSelect.add(new Option(provider,provider));if(desired?.provider)providerSelect.value=desired.provider;fillModels(desired?.model);authSelect.value=desired?.credential_source??'pi_managed';configNote.textContent='Remembered per workflow on this browser.';}catch(error){configNote.textContent=error.message;}}
  providerSelect.addEventListener('change',()=>fillModels());
  configForm.addEventListener('submit',event=>{event.preventDefault();const claude=agentSelect.value==='claude_code',config=claude?{version:1,agent:'claude_code',model:modelSelect.value,effort:thinkingSelect.value}:{version:1,provider:providerSelect.value,model:modelSelect.value,thinking_level:thinkingSelect.value,credential_source:authSelect.value};if(!config.model||(!claude&&!config.provider))return;try{if(ready){checkWorkflow();const switching=claude!==(activeConfig?.agent==='claude_code');if(switching||(claude&&config.effort!==activeConfig?.effort)||(!claude&&(config.credential_source!==activeConfig?.credential_source||(config.credential_source==='studio_keychain'&&config.provider!==activeConfig?.provider)))){remember({pi_config:config});configNote.textContent=switching?'Saved. Start New chat to switch the agent.':'Saved. Start New chat to use it.';return;}configuring=true;send({type:'configure',pi_config:config});configNote.textContent='Applying…';update();}else{remember({pi_config:config});model.textContent=configLabel(config);showConnection();modelPicker.open=false;}}catch(error){configuring=false;configNote.textContent=error.message;update();}});
  root.refreshWorkflowPreferences=()=>{refreshRunOption();showConnection();if(!ready){const saved=preferences().pi_config;model.textContent=configLabel(saved);}else if(boundWorkflow!==workflowId())status.textContent='Workflow changed. Start New chat to use its settings.';};
  root.refreshWorkflowPreferences();
  function transmitPrompt(text) {try{checkWorkflow();}catch(error){row('notice',error.message);status.textContent=error.message;update();return;}row('user',text);archiveMessage('user',text);archiveAssistant=null;assistant=null;stopped=false;busy=true;turnSubmitted=false;turnCompleted=false;turnFailed=false;turnWrites=false;update();status.textContent=`${agentName} is working…`;delete status.dataset.settled;send({type:'prompt',message:text});}
  function connect(text) {
    const id=workflowId();if(typeof id!=='string'||!id)throw Error('Load a native workflow before connecting Pi.');boundWorkflow=id;
    const config=requestedConfig(),url=new URL(api.apiURL('/agent-workflow-studio/pi-chat/ws'),location.href);url.protocol=url.protocol==='https:'?'wss:':'ws:';
    const current=++generation;connecting=true;pendingText=text;update();agentName=config?.agent==='claude_code'?'Claude':'Pi';status.textContent=`Connecting to ${agentName}…`;socket=new WebSocket(url);
    socket.addEventListener('open',()=>{if(current===generation)send({type:'start',pi_config:config,workflow_id:boundWorkflow});});
    socket.addEventListener('message',async event=>{
      if(current!==generation)return;
      const record=JSON.parse(event.data);
      if(record.type==='ready'){
        if(record.workspace?.graph_id!==boundWorkflow){row('notice','Pi workspace identity did not match this workflow.');socket.close();return;}
        workspace.textContent=`Files · ${record.workspace.workflow_id.slice(0,8)} · shell off`;workspace.title=record.workspace.root;filesSupported=record.workspace_files_ui===true;refreshFiles.disabled=!filesSupported;if(!filesSupported)filesNote.textContent='Workspace file downloads need the updated backend and New chat.';
        showAddons(record.extensions);skills.textContent=`ComfyUI · ${record.skills.names.length} skills · default`;skills.title=record.skills.names.join(' · ');ready=true;connecting=false;activeConfig=record.pi_config??{version:1,provider:record.model?.provider,model:record.model?.id,thinking_level:'off',credential_source:record.auth_source};refreshRunOption();model.textContent=record.agent==='claude_code'?configLabel(activeConfig):[record.model?.provider,record.model?.id,record.auth_source].filter(Boolean).join(' · ')||'Pi defaults';
        showConnection();const queued=pendingText;pendingText=null;update();if(queued)transmitPrompt(queued);else status.textContent=`Connected to ${agentName}.`;if(filesSupported)requestFiles();
      }else if(record.type==='workspace_saved'){turnWrites=true;if(filesSupported)requestFiles();
      }else if(record.type==='workspace_files'||record.type==='workspace_file'){try{checkWorkflow();if(record.error)throw Error(record.error);if(record.type==='workspace_files')showFiles(record.result);else downloadFile(record.result);}catch(error){filesNote.textContent=error.message;}
      }else if(record.type==='model_config'){configuring=false;activeConfig=record.pi_config;model.textContent=configLabel(activeConfig);showConnection();try{checkWorkflow();remember({pi_config:activeConfig});modelPicker.open=false;}catch(error){configNote.textContent=error.message;}status.textContent='Model updated';update();
      }else if(record.type==='extensions_status')showAddons(record.extensions);
      else if(record.type==='graph_check')row('notice',record.text);
      else if(record.type==='agent_start'){busy=true;update();status.textContent=`${agentName} is working…`;}
      else if(record.type==='message_start'&&record.message?.role==='assistant'){archiveAssistant=null;assistant=null;}// the row appears with its first text: tool-only steps add none
      else if(record.type==='message_update'&&record.assistantMessageEvent?.type==='text_delta'){
        assistant??=row('assistant','');assistant.textContent+=record.assistantMessageEvent.delta;archiveReply(record.assistantMessageEvent.delta,false);scroll();
      }else if(record.type==='message_end'&&record.message?.role==='assistant'){
        const text=textOf(record.message);archiveReply(text,true,['aborted','error'].includes(record.message.stopReason));if(text){assistant??=row('assistant','');if(app.extensionManager.renderMarkdownToHtml)assistant.innerHTML=app.extensionManager.renderMarkdownToHtml(text);else assistant.textContent=text;}
        if(record.message.errorMessage)row('notice',record.message.errorMessage);
      }else if(record.type==='tool_execution_start'){
        const details=element('details',null,{className:'aw-pi-tool',title:record.toolName}),summary=element('summary',`${toolPhrase(record.toolName)}…`),body=element('pre',JSON.stringify(record.args??{},null,2));details.dataset.tool=record.toolName;details.append(summary,body);log.append(details);toolRows.set(record.toolCallId,{summary,body});status.textContent=`${toolPhrase(record.toolName)}…`;scroll();
      }else if(record.type==='tool_execution_end'){
        const tool=toolRows.get(record.toolCallId);if(tool){tool.summary.textContent=`${record.isError?'Failed':'Done'} · ${toolPhrase(record.toolName)}`;tool.body.textContent=textOf(record.result).slice(0,16000);}scroll();
      }else if(record.type==='comfy_action'){
        try{checkWorkflow();const result=await bridge.handle(record.action,record.args);if(record.action==='edit')await commitWorkflow(record.args?.summary||'Agent canvas edit','agent');if(record.action==='build')await commitWorkflow(record.args?.summary||`Build ${record.args?.step} from its LLD`,'agent');if(record.action==='harden')await commitWorkflow(`Harden (approved): ${record.args?.node}`,'user');if(current===generation&&busy&&!stopped){if(record.action==='run'&&result.submitted){turnSubmitted=true;turnCompleted=false;turnFailed=false;}// the footer reports the latest run
        if(record.action==='results'&&turnSubmitted&&result.submitted&&result.status?.completed===true&&result.status?.status_str==='success'){turnCompleted=true;turnFailed=false;}if(record.action==='results'&&turnSubmitted&&result.submitted&&result.status?.status_str==='error'){turnFailed=true;turnCompleted=false;}if(record.action==='results')showNativeFiles(result);}if(current===generation&&busy&&!stopped&&socket.readyState===WebSocket.OPEN)send({type:'comfy_result',id:record.id,result});}
        catch(error){if(current===generation&&busy&&!stopped&&socket.readyState===WebSocket.OPEN)send({type:'comfy_result',id:record.id,error:error.message});}
      }else if(record.type==='extension_ui_request'){
        if(['confirm','select','input','editor'].includes(record.method))permission(record,(response,notify)=>{if(notify!==false&&current===generation&&socket.readyState===WebSocket.OPEN)send({type:'extension_ui_response',id:record.id,...response});});
        else if(record.method==='notify'){if(['warning','error'].includes(record.notifyType))row('notice',record.message);else notices.append(element('small',record.message));}
        else if(record.method==='setStatus'&&record.statusKey==='comfy-addons'){try{showAddons(JSON.parse(record.statusText));}catch{}}
        else if(record.method==='set_editor_text')input.value=record.text??'';
      }else if(record.type==='agent_settled'||record.type==='command_handled'){
        busy=false;if(record.type==='agent_settled')archiveMessage('notice',executionSummary());else saveArchive();status.textContent=record.type==='command_handled'?'Local Pi command finished.':finishedText({failed:turnFailed,completed:turnCompleted,submitted:turnSubmitted});status.dataset.settled=executionSummary();if(filesSupported)requestFiles();settleDialogs();update();scroll();
      }else if(record.type==='chat_error'||(record.type==='response'&&record.success===false)){
        row('notice',record.error??'Pi command failed.');status.textContent=record.error??'Pi command failed.';configNote.textContent=record.error??'Pi command failed.';configuring=false;busy=false;connecting=false;pendingText=null;settleDialogs();update();
      }
    });
    socket.addEventListener('close',event=>{if(current!==generation)return;ready=false;connecting=false;configuring=false;busy=false;pendingText=null;saveArchive();refreshFiles.disabled=false;filesSupported=false;filesList.replaceChildren();filesSummary.textContent='Workflow files';files.hidden=true;showConnection();settleDialogs();status.textContent=`${agentName} disconnected${event.code&&event.code!==1000?` (code ${event.code})`:''}. Send a message to start a new session.`;update();});
    socket.addEventListener('error',()=>{if(current===generation)status.textContent='Cannot connect to Pi. Check the local ComfyUI/Pi backend.';});
  }
  async function reset() {if(configuring||resetting)return;if(busy&&!await confirm('Start a new chat?','Stop Pi and clear this conversation? Your native canvas will be preserved.'))return;resetting=true;update();if(busy){generation++;settleDialogs();socket?.close();socket=null;ready=false;connecting=false;busy=false;}await saveArchive();if(archiveError&&!await confirm('Start without saving chat history?','History could not be saved. Copy this conversation first. Start New chat and discard its unsaved messages?')){resetting=false;update();return;}archive=null;archiveAssistant=null;generation++;settleDialogs();socket?.close();socket=null;ready=false;connecting=false;busy=false;pendingText=null;assistant=null;toolRows.clear();nativeOutputs.clear();filesSupported=false;refreshFiles.disabled=false;filesList.replaceChildren();filesSummary.textContent='Workflow files';filesNote.textContent='Workspace files are not native node execution outputs.';log.replaceChildren(empty);status.textContent='New chat. Your canvas is unchanged.';addons.textContent='Extensions load on connect';addons.title='';skills.textContent='ComfyUI skills load on connect';skills.title='';boundWorkflow=null;activeConfig=undefined;configuring=false;refreshRunOption();model.textContent=configLabel(preferences().pi_config);agentName='Pi';workspace.textContent='Files bind to the workflow on connect';workspace.title='';files.hidden=true;notices.replaceChildren();attachments=[];showAttached();resetting=false;showConnection();update();}
  controls.append(button('New chat',reset,'pi-plus'),button('History',openHistory,'pi-history'));connection.append(modelPicker,addons,skills,workspace,runOption,button('Credentials',openPiSettings,'pi-key'),notices);header.append(heading,controls,connection);showConnection();
  // Starters fill the box (not send), so the user adds their own details first.
  for(const text of ['Total my sales per region from a CSV file','Find duplicate customer records in my data','Explain the workflow on my canvas'])suggestions.append(button(text,()=>{input.value=text;update();input.focus();}));empty.append(suggestions);log.append(empty);
  // Files for the agent: ComfyUI's own upload into the workflow's input folder (as the Data folder node does).
  const picker=element('input',null,{type:'file',multiple:true,hidden:true}),attached=element('div',null,{className:'aw-pi-attached',hidden:true}),attachButton=button('Attach files',()=>picker.click(),'pi-paperclip');attachButton.title='Attach files (or drop them here): they go into ComfyUI\'s input folder, where the workflow reads them';
  let attachments=[];
  function showAttached(){attached.replaceChildren(...attachments.map(path=>{const chip=element('span',null,{className:'aw-pi-chip',title:`In ComfyUI's input folder: ${path}`});chip.append(element('i',null,{className:'pi pi-file'}),element('span',path));return chip;}));attached.hidden=!attachments.length;}
  async function attach(list){const chosen=[...list];if(!chosen.length)return;const folder=workflowFolder();try{status.textContent=`Uploading ${chosen.length} file${chosen.length>1?'s':''}…`;await uploadFolder(chosen,folder);await app.refreshComboInNodes?.();for(const file of chosen)if(!attachments.includes(`${folder}/${file.name}`))attachments.push(`${folder}/${file.name}`);showAttached();const picked=chooseFolder(folder);status.textContent=`Attached in ComfyUI's input folder, ${folder}/${picked?`, and chose it on ${picked}`:''}. Send a message to tell the agent.`;update();}catch(error){row('notice',`Upload failed: ${error.message}`);}finally{picker.value='';}}
  // The workflow's one data input (a Data folder the contract promised) takes the folder just attached,
  // as its own Upload button does; with several, the agent chooses.
  function chooseFolder(folder){const inputs=(app.rootGraph?.nodes??[]).filter(n=>n.type==='AgentWorkflowDataFolder'&&n.properties?.aws_input);const widget=inputs.length===1&&inputs[0].widgets?.find(w=>w.name==='folder');if(!widget)return null;widget.value=folder;widget.callback?.(folder);app.graph.setDirtyCanvas(true,true);return inputs[0].title;}
  picker.addEventListener('change',()=>attach(picker.files));
  root.addEventListener('dragover',event=>{if(event.dataTransfer?.types?.includes('Files')){event.preventDefault();event.stopPropagation();root.classList.add('aw-pi-drop');}});
  root.addEventListener('dragleave',event=>{if(!root.contains(event.relatedTarget))root.classList.remove('aw-pi-drop');});
  root.addEventListener('drop',event=>{if(!event.dataTransfer?.files?.length)return;event.preventDefault();event.stopPropagation();root.classList.remove('aw-pi-drop');attach(event.dataTransfer.files);});// not ComfyUI's drop (it would open the file as a workflow)
  actions.append(attachButton,hint,stopButton,sendButton);form.append(attached,input,actions,archiveNote,picker);form.addEventListener('submit',event=>{event.preventDefault();const typed=input.value.trim();const text=typed&&attachments.length?`${typed}\n\n(Attached, in ComfyUI's input folder: ${attachments.join(', ')})`:typed;if(!text||busy||connecting||configuring||resetting)return;try{if(ready)transmitPrompt(text);else connect(text);input.value='';attachments=[];showAttached();update();}catch(error){row('notice',error.message);update();}});
  input.addEventListener('input',update);input.addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();event.stopPropagation();form.requestSubmit();}});
  root.append(header,files,log,status,form,historyDialog);update();
  window.addEventListener('pagehide',()=>{saveArchive();generation++;settleDialogs();socket?.close();bridge.dispose();},{once:true});
  return root;
}

if(host?.registerSidebarTab){
  let chat;
  let opened=false;
  app.registerExtension({name:'AgentWorkflowStudio.PiChat',setup(){
    document.head.append(element('link',null,{rel:'stylesheet',href:new URL('./pi_chat.css',import.meta.url).href}));
    host.registerSidebarTab({id:'pi-chat',icon:'pi pi-comments',title:'Agent',tooltip:'Agent · describe what you want, it builds the workflow',type:'custom',render:container=>{container.classList.add('aw-pi-host');chat??=createChat();container.replaceChildren(chat);}});
  },
  // A first look: on an empty canvas with no sidebar open, the agent is where to start.
  async afterConfigureGraph(){
    chat?.refreshWorkflowPreferences();
    if(opened)return;opened=true;
    await new Promise(resolve=>setTimeout(resolve,500));
    const sidebar=app.extensionManager?.sidebarTab;
    if(!(app.rootGraph?.nodes?.length)&&!sidebar?.activeSidebarTabId)void app.extensionManager?.command?.execute('Workspace.ToggleSidebarTab.pi-chat');
  }});
}
