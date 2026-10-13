/** Pi's native extension API + RPC dialog protocol; no custom agent loop. */
import { Type } from "@earendil-works/pi-ai";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineTool, loadSkillsFromDir, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { loadChatAddons } from "./pi_chat_addons.ts";
import { registerWorkspaceFiles } from "./pi_chat_files.ts";

const ref = Type.Union([Type.String(), Type.Number()]);
const operation = Type.Union([
  Type.Object({ op: Type.Literal("add"), key: Type.String(), class_type: Type.String(), title: Type.Optional(Type.String()), position: Type.Optional(Type.Tuple([Type.Number(), Type.Number()])) }),
  Type.Object({ op: Type.Literal("move"), node: ref, position: Type.Tuple([Type.Number(), Type.Number()]) }),
  Type.Object({ op: Type.Literal("title"), node: ref, title: Type.String({maxLength:200}) }),
  Type.Object({ op: Type.Literal("widget"), node: ref, name: Type.String(), value: Type.Unknown() }),
  Type.Object({ op: Type.Literal("connect"), from: ref, output: Type.Integer({minimum:0}), to: ref, input: Type.Union([Type.String(), Type.Integer({minimum:0})]) }),
  Type.Object({ op: Type.Literal("remove"), node: ref }),
  Type.Object({ op: Type.Literal("disconnect"), node: ref, input: Type.Union([Type.String(), Type.Integer({minimum:0})]) }),
]);

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
  if(loaded.diagnostics.length||loaded.skills.length!==4) throw new Error("Bundled ComfyUI skills could not be verified.");
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
    if (["comfy_workspace","comfy_read_workflow","comfy_node_catalog","comfy_edit_workflow","comfy_run_workflow","comfy_execution_results"].includes(event.toolName)) return;
    const approved = await ctx.ui.confirm(`Allow Pi tool: ${event.toolName}?`, JSON.stringify(event.input).slice(0,4000));
    if (!approved) return {block:true, reason:"The user did not authorize this external tool."};
  });
  pi.registerCommand("comfy", {description:"Show native canvas/MCP tool availability without a provider call.", handler: async (args,ctx) => {
    const tools={type:"comfy_tools",active:pi.getActiveTools(),mcp:pi.getAllTools().filter(tool=>tool.name.startsWith("mcp__")).map(tool=>tool.name),extensions:addons(ctx),workspace:workspace(),skills,schema_only_prompt:ctx.getSystemPrompt().includes("You are a bounded structured-output assistant.")};
    ctx.ui.notify(args==="--check"?JSON.stringify(tools):`Connected Pi tools: ${tools.active.join(", ")}`,"info");
  }});
  pi.registerTool(defineTool({
    name:"comfy_read_workflow", label:"Inspect canvas", description:"Read the current live native canvas, selected nodes, widgets and links. Private approval properties and credential fields are excluded.",
    parameters:Type.Object({}), execute:async (_id,args,signal) => canvas("read",args,signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_node_catalog", label:"Find native nodes", description:"Search ALL installed native node types by name/category, descriptions, aliases and input/output schemas (including format choices such as csv), or retrieve exact schemas for class_types. Discovery does not install or authorize generated code.",
    parameters:Type.Object({query:Type.Optional(Type.String()),class_types:Type.Optional(Type.Array(Type.String(),{maxItems:20}))}),
    execute:async (_id,args,signal) => canvas("catalog",args,signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_edit_workflow", label:"Edit native canvas", description:"Apply up to 32 native canvas edits atomically with native undo support, including move/title layout edits that preserve node identity and links. New-node keys can be used by later operations in the batch. Widget values must match their native schema. Never clears the canvas. Removal, disconnection and replacing occupied inputs require confirmation.",
    parameters:Type.Object({operations:Type.Array(operation,{minItems:1,maxItems:32}),summary:Type.Optional(Type.String({maxLength:200,description:"One line saying what this batch does and why; recorded as the version message."}))}),
    execute:async (_id,args,signal) => {
      // The native browser verifies existing links and asks once for all
      // destructive changes, then refuses edits if the canvas changed meanwhile.
      return canvas("edit",args,signal);
    },
  }));
  pi.registerTool(defineTool({
    name:"comfy_run_workflow", label:"Run native workflow", description:"Queue the current workflow using ComfyUI's native Run path, including its validation. The browser requires confirmation unless the user enabled auto-approve runs for this workflow. Returns submission status, not proof of useful results.",
    parameters:Type.Object({reason:Type.String()}), execute:async (_id,args,signal) => {
      return canvas("run",args,signal);
    },
  }));
  pi.registerTool(defineTool({
    name:"comfy_execution_results", label:"Inspect execution", description:"Read native queue state and outputs/errors for the latest run submitted by this chat. If it is still running, report that; do not repeatedly poll in a tight loop.",
    parameters:Type.Object({}), execute:async (_id,args,signal) => canvas("results",args,signal),
  }));
}
