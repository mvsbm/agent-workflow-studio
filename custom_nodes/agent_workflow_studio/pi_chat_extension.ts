/** Pi's native extension API + RPC dialog protocol; no custom agent loop. */
import { Type } from "@earendil-works/pi-ai";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineTool, loadSkillsFromDir, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { loadChatAddons } from "./pi_chat_addons.ts";
import { registerWorkspaceFiles } from "./pi_chat_files.ts";

// The agent's tools are one JSON file shared by every chat backend (Pi here, Claude Code through
// claude_mcp.py): name, description, JSON Schema parameters and the studio action each one calls.
const TOOLS: {name:string;label:string;description:string;parameters:any;action:string}[] = JSON.parse(readFileSync(fileURLToPath(new URL("./agent_tools.json",import.meta.url)),"utf8"));
// The contract schema is one JSON Schema file shared with the server and editors. Tool schemas
// get it with the $ref inlined and editor-only keys removed, which some providers reject.
const contractSchema = (() => {
  const {$schema: _s, $id: _i, title: _t, $defs, ...schema} = JSON.parse(readFileSync(fileURLToPath(new URL("./contract.schema.json",import.meta.url)),"utf8"));
  schema.properties.contract = {enum:[2,"2"]};  // some models send the marker as text; the server stamps 2
  return JSON.parse(JSON.stringify(schema).replaceAll('{"$ref":"#/$defs/port"}',JSON.stringify($defs.port)));
})();
const OTHER = "Other (I'll type my answer)";

async function request(action: string, args: unknown, signal?: AbortSignal) {
  const url = process.env.COMFY_PI_BRIDGE_URL, token = process.env.COMFY_PI_BRIDGE_TOKEN;
  if (!url || !token) throw new Error("This tool requires the native ComfyUI Pi chat session.");
  const response = await fetch(url, {
    method: "POST", headers: {"Content-Type":"application/json", "Authorization":`Bearer ${token}`},
    body: JSON.stringify({action, args}), signal,
  });
  const body = await response.json() as {result?: unknown; error?: string};
  if (!response.ok) throw new Error(body.error || "ComfyUI action failed.");
  return body.result;
}

async function canvas(action: string, args: unknown, signal?: AbortSignal) {
  const result = await request(action,args,signal) as Record<string, unknown>;
  const previews = result?.image_previews as {mimeType:string;data:string}[] | undefined;
  if (previews) delete result.image_previews;
  return {content: [{type:"text" as const, text:JSON.stringify(result)}, ...(previews??[]).map(image=>({type:"image" as const, ...image}))], details: result};
}

export default async function (pi: ExtensionAPI) {
  const addons = await loadChatAddons(pi);
  const workspace = registerWorkspaceFiles(pi,request);
  // Only bundled, trusted skills: never discover instructions in editable workspaces.
  const skillDirectory=fileURLToPath(new URL("./skills/",import.meta.url));
  const loaded=loadSkillsFromDir({dir:skillDirectory,source:"comfyui-chat"});
  if(loaded.diagnostics.length||loaded.skills.length!==6) throw new Error("Bundled ComfyUI skills could not be verified.");
  const skills={names:loaded.skills.map(skill=>skill.name).sort(),default_loaded:true};
  // The environment/tool guide lives in prompts/ so it can be read and reviewed as Markdown.
  const agentPrompt=readFileSync(fileURLToPath(new URL("./prompts/comfyui_agent.md",import.meta.url)),"utf8");
  const skillInstructions=loaded.skills.map(skill=>readFileSync(skill.filePath,"utf8")).join("\n\n");
  pi.on("resources_discover",()=>({skillPaths:[skillDirectory]}));
  pi.on("before_agent_start", async (event,ctx) => {
    ctx.ui.setStatus("comfy-addons",JSON.stringify(addons(ctx)));
    const instructions = agentPrompt
      .replaceAll("{{WORKFLOW_ID}}", process.env.COMFY_PI_WORKFLOW_ID ?? "")
      .replaceAll("{{WORKSPACE_DIR}}", process.env.COMFY_PI_WORKSPACE_DIR ?? "");
    // Preserve Ponytail and Pi's other structured prompt sections/cached prefix.
    event.systemPromptOptions.sections.comfyui = instructions;
    // Scoped file tools cannot read host skill paths. Include trusted bodies
    // eagerly so every normal turn uses them without a /skill command or file access.
    event.systemPromptOptions.sections.comfyui_skills = `Apply the following ComfyUI skills by default whenever relevant. Their full contents are already loaded here: use them directly, not by trying to read host skill paths with workspace file tools. They guide native tools; they do not grant execution or installation authority.\n\n${skillInstructions}`;
  });

  // Native MCP tools are still Pi tools. Unknown external effects require a
  // real RPC permission answer; the agent cannot answer its own dialog.
  pi.on("tool_call", async (event, ctx) => {
    if (["bash","powershell","grep","find","ls"].includes(event.toolName)||Object.hasOwn(event.input??{},"then_run")) return {block:true,reason:"Host shell/search and then_run are disabled. Use the workspace file tools and existing managed execution."};
    if (["read","edit","write"].includes(event.toolName)) {workspace();return;}
    if (TOOLS.some(tool=>tool.name===event.toolName)) return;
    const approved = await ctx.ui.confirm(`Allow Pi tool: ${event.toolName}?`, JSON.stringify(event.input).slice(0,4000));
    if (!approved) return {block:true, reason:"The user did not authorize this external tool."};
  });
  pi.registerCommand("comfy", {description:"Show native canvas/MCP tool availability without a provider call.", handler: async (args,ctx) => {
    const tools={type:"comfy_tools",active:pi.getActiveTools(),mcp:pi.getAllTools().filter(tool=>tool.name.startsWith("mcp__")).map(tool=>tool.name),extensions:addons(ctx),workspace:workspace(),skills,schema_only_prompt:ctx.getSystemPrompt().includes("You are a bounded structured-output assistant.")};
    ctx.ui.notify(args==="--check"?JSON.stringify(tools):`Connected Pi tools: ${tools.active.join(", ")}`,"info");
  }});
  for (const tool of TOOLS) {
    // The agent schemas (intent, design, build) are generated already inlined by workflow_contract.py.
    const parameters = Type.Unsafe(JSON.parse(JSON.stringify(tool.parameters).replace(/\{"\$ref":"(\w+)\.schema\.json"\}/g, (_, name) =>
      name === "contract" ? JSON.stringify(contractSchema) : readFileSync(fileURLToPath(new URL(`./${name}.schema.json`, import.meta.url)), "utf8"))));
    pi.registerTool(defineTool({
      name:tool.name, label:tool.label, description:tool.description, parameters,
      execute:async (_id,args:any,signal,_update,ctx) => {
        if (tool.action !== "ask_user") return canvas(tool.action,args,signal);
        const answers:{question:string;answer:string}[]=[];
        for (const {question,options} of args.questions) {
          let answer=await ctx.ui.select(question,[...options,OTHER]);
          if (answer===OTHER) answer=await ctx.ui.input(question,"Type your answer");
          if (answer===undefined) {
            const result={answers,dismissed:true,message:"The user dismissed the questions. Do not assume answers; ask in chat what they want."};
            return {content:[{type:"text" as const,text:JSON.stringify(result)}],details:result};
          }
          answers.push({question,answer});
        }
        return {content:[{type:"text" as const,text:JSON.stringify({answers})}],details:{answers}};
      },
    }));
  }
}
