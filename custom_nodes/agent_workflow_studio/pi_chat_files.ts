/** Pi's native file tools, with every filesystem operation delegated to the
 * authenticated workspace broker. Never pass through to the host filesystem. */
import { relative, resolve } from "node:path";
import { createReadToolDefinition, createEditToolDefinition, createWriteToolDefinition, type ExtensionAPI, type ExtensionContext, type ToolDefinition } from "@earendil-works/pi-coding-agent";

type Call = (action:string,args:unknown,signal?:AbortSignal)=>Promise<any>;
export function registerWorkspaceFiles(pi:ExtensionAPI,call:Call) {
  const root=process.env.COMFY_PI_WORKSPACE_DIR,id=process.env.COMFY_PI_WORKFLOW_ID;
  if (!root||!id) throw new Error("A workflow-bound file workspace is required.");
  const directory=resolve(root),hashes=new Map<string,string>();
  const path=(absolute:string)=>relative(directory,absolute).split("\\").join("/");
  const load=async(absolute:string)=>{
    const result=await call("workspace_read",{path:path(absolute)});
    return {bytes:Buffer.from(result.data,"base64"),hash:result.sha256 as string};
  };
  const access=async(absolute:string)=>{
    const result=await call("workspace_stat",{path:path(absolute)});
    if (!result.exists) throw new Error("Workspace file does not exist.");
  };
  let ctx:ExtensionContext;
  // Registered after the optional add-ons load, so these confined tools replace
  // Pi's built-in (and SoL-Pi's, when installed) unrestricted file tools.
  pi.on("session_start",(_event,context)=>{
    ctx=context;
    const read=createReadToolDefinition(directory,{operations:{access,readFile:async absolute=>(await load(absolute)).bytes,detectImageMimeType:async()=>null}});
    const edit=createEditToolDefinition(directory,{operations:{access,readFile:async absolute=>{const value=await load(absolute);hashes.set(absolute,value.hash);return value.bytes;},writeFile:async(absolute,content)=>{
      const expected=hashes.get(absolute);if(!expected)throw new Error("Read the workspace file before editing.");
      await call("workspace_write",{path:path(absolute),data:Buffer.from(content).toString("base64"),expected_sha256:expected});hashes.delete(absolute);
    }}});
    const write=createWriteToolDefinition(directory,{operations:{mkdir:async()=>{},writeFile:async(absolute,content)=>{
      const before=await call("workspace_stat",{path:path(absolute)});
      if(before.exists&&!await ctx.ui.confirm("Overwrite workspace file?",path(absolute),{timeout:90000}))throw new Error("The user declined this overwrite.");
      await call("workspace_write",{path:path(absolute),data:Buffer.from(content).toString("base64"),expected_sha256:before.sha256});
    }}});
    function register<Params extends ToolDefinition["parameters"],Details,Render>(tool:ToolDefinition<Params,Details,Render>) {
      const execute=tool.execute;
      // Pi resolves native tool paths against execution ctx.cwd, not the
      // factory cwd. Override only this call; never discover workspace config.
      tool.execute=async(id,args,signal,update,context)=>{
        const result=await execute(id,args,signal,update,{...context,cwd:directory});
        if(tool.name==="write"||tool.name==="edit")result.content.push({type:"text",text:"Saved a workspace file only, not a ComfyUI node execution output. This does not run the workflow or prove dataset generation. The user can download it from Workflow files."});
        return result;
      };
      tool.description="Confined to this workflow's persistent workspace; use relative paths. No shell/then_run. "+tool.description;
      tool.namespace={name:"comfy_workspace",description:"Confined workflow files; no host filesystem or code execution."};
      pi.registerTool(tool);
    }
    register(read);register(edit);register(write);
    pi.setActiveTools([...new Set([...pi.getActiveTools(),"read","edit","write"])]);
  });
  return ()=>{
    const tools=pi.getAllTools();
    if(!["read","edit","write"].every(name=>pi.getActiveTools().includes(name)&&tools.some(tool=>tool.name===name&&tool.namespace?.name==="comfy_workspace")))throw new Error("Workspace file tools were not safely initialized.");
    return {workflow_id:id,graph_id:process.env.COMFY_PI_WORKFLOW_GRAPH_ID??id,root:directory,file_tools_scoped:true,shell_enabled:false};
  };
}
