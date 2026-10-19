/** Pi's native extension API + RPC dialog protocol; no custom agent loop. */
import { Type } from "@earendil-works/pi-ai";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineTool, loadSkillsFromDir, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { loadChatAddons } from "./pi_chat_addons.ts";
import { registerWorkspaceFiles } from "./pi_chat_files.ts";

const ref = Type.Union([Type.String(), Type.Number()]);
// [x, y]. Not a TypeBox tuple: its array-form "items" is invalid JSON Schema 2020-12, which some providers enforce.
const xy = Type.Array(Type.Number(), {minItems:2, maxItems:2});
const port = Type.Union([Type.String(), Type.Integer({minimum:0})]);
// Where an operation works: inside a stage (a contract step id), or without it the top level, which
// holds only the stages and the user's given nodes. Inside a stage, "input" / "output" name its ports.
const stage = Type.Optional(Type.String({pattern:"^[a-z][a-z0-9_]{0,31}$", description:"Contract step id: the stage this operation works inside. Omit for the top level (connect stages and given nodes)."}));
const operation = Type.Union([
  Type.Object({ op: Type.Literal("add"), stage: Type.String({pattern:"^[a-z][a-z0-9_]{0,31}$", description:"The stage (contract step id) the node goes in."}), key: Type.String(), class_type: Type.String(), title: Type.Optional(Type.String()), position: Type.Optional(xy) }),
  Type.Object({ op: Type.Literal("move"), stage, node: ref, position: xy }),
  Type.Object({ op: Type.Literal("title"), stage, node: ref, title: Type.String({maxLength:200}) }),
  Type.Object({ op: Type.Literal("widget"), stage, node: ref, name: Type.String(), value: Type.Unknown() }),
  Type.Object({ op: Type.Literal("connect"), stage, from: Type.Union([ref, Type.Literal("input")], {description:'A node key/id; inside a stage "input" is its input ports (output = the port name). At the top level a step id names its stage.'}), output: port, to: Type.Union([ref, Type.Literal("output")], {description:'A node key/id; inside a stage "output" is its output ports (input = the port name).'}), input: port }),
  Type.Object({ op: Type.Literal("remove"), stage, node: ref }),
  Type.Object({ op: Type.Literal("disconnect"), stage, node: ref, input: port }),
  // Re-lay out the canvas (also done automatically after adds and connects).
  Type.Object({ op: Type.Literal("arrange") }),
]);

// The contract schema is one JSON Schema file shared with the server and editors. Tool schemas
// get it with the $ref inlined and editor-only keys removed, which some providers reject.
const contractSchema = (() => {
  const {$schema: _s, $id: _i, title: _t, $defs, ...schema} = JSON.parse(readFileSync(fileURLToPath(new URL("./contract.schema.json",import.meta.url)),"utf8"));
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
  if(loaded.diagnostics.length||loaded.skills.length!==5) throw new Error("Bundled ComfyUI skills could not be verified.");
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
    if (["comfy_workspace","comfy_read_workflow","comfy_node_catalog","comfy_edit_workflow","comfy_run_workflow","comfy_execution_results","comfy_ask_user","comfy_propose_contract","comfy_contract_status","comfy_check_gates","comfy_propose_tool","comfy_analyze_graph","comfy_find_converter","comfy_input_files"].includes(event.toolName)) return;
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
    parameters:Type.Object({query:Type.Optional(Type.String()),queries:Type.Optional(Type.Array(Type.String(),{maxItems:8,description:"Several searches at once (up to 8), each returning up to 20 matches."})),class_types:Type.Optional(Type.Array(Type.String(),{maxItems:20}))}),
    execute:async (_id,args,signal) => canvas("catalog",args,signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_edit_workflow", label:"Edit native canvas", description:"Apply up to 32 native canvas edits atomically with native undo support, including move/title layout edits that preserve node identity and links. Nodes live inside stages (one per contract step): give \"stage\" for every operation inside one; inside, \"input\"/\"output\" are the stage's ports by name; build a whole stage per batch: its nodes wired from its input ports to its output ports (the Stub disappears and the Check follows by itself; stages are already wired to each other). Without \"stage\", operations work at the top level, which holds only stages (named by step id) and the user's given nodes. New-node keys stay valid for the rest of this chat, so later batches can reference earlier nodes by key. Widget values must match their native schema. Never clears the canvas. Removal, disconnection and replacing occupied inputs require confirmation.",
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
    name:"comfy_execution_results", label:"Inspect execution", description:"Read native queue state and outputs/errors for the latest run submitted by this chat. Waits up to 60 s for that run to finish; if it is still running after that, call it again rather than stopping.",
    parameters:Type.Object({}), execute:async (_id,args,signal) => canvas("results",args,signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_ask_user", label:"Ask the user", description:"Ask the user up to 4 multiple-choice questions and wait for the answers. Each question offers 2-4 short options plus a free-text 'Other'. Use while planning to settle requirements; ask everything you need at once.",
    parameters:Type.Object({questions:Type.Array(Type.Object({question:Type.String({minLength:1,maxLength:300}),options:Type.Array(Type.String({minLength:1,maxLength:200}),{minItems:2,maxItems:4})}),{minItems:1,maxItems:4})}),
    execute:async (_id,args,_signal,_update,ctx) => {
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
  pi.registerTool(defineTool({
    name:"comfy_propose_contract", label:"Propose contract", description:"Propose the workflow contract and wait for the user's approval in the chat. Required before adding nodes, and again to amend it. Purpose, requests (the user's words + your restatement + priority), examples, promised inputs/outputs, steps, decisions, not-now, open questions (must be empty). Each step is a stage: give its typed ports (takes: an earlier step's output 'step.port' or a type for data entering; gives: port -> type) and a gate whose check names one of its outputs. On approval the canvas gets the skeleton: one wired stage per step with a Stub inside and a Check per gate.",
    parameters:Type.Object({contract:Type.Unsafe(contractSchema)}),
    execute:async (_id,args,signal) => canvas("contract_propose",{contract:args.contract},signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_check_gates", label:"Check success gates", description:"After a run (waits up to 60 s for it to finish), every step's gate from the Check nodes inside its stage on the latest run: passed, failed (with the evidence), or needs_user_review when the stage has no Check.",
    parameters:Type.Object({}),
    execute:async (_id,_args,signal) => canvas("contract_gates",{},signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_analyze_graph", label:"Analyze graph", description:"Check the live canvas without running it or calling a model: output nodes, nodes that will not run (they reach no output), unconnected required inputs, type mismatches, cycles, deprecated nodes, and per output the checkpoints every path must pass (good places for success gates). Runs automatically after structural edits.",
    parameters:Type.Object({}), execute:async (_id,args,signal) => canvas("analyze",args,signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_find_converter", label:"Find converter", description:"Find the shortest chains of installed nodes that turn one data type into another (e.g. IMAGE to MASK), each step a node with one connected input.",
    parameters:Type.Object({from_type:Type.String({minLength:1,maxLength:80}),to_type:Type.String({minLength:1,maxLength:80})}),
    execute:async (_id,args,signal) => canvas("converters",args,signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_input_files", label:"Input files", description:"Look at data the user put in ComfyUI's input folder, read-only: without a path, list its files; with a path, show a text file's encoding, size, line count and first 60 lines. An Agent Tool node reads such a folder through its Input folder widget (TOOL_INPUT_DIR).",
    parameters:Type.Object({path:Type.Optional(Type.String({maxLength:300}))}),
    execute:async (_id,args,signal) => canvas("input_files",args,signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_contract_status", label:"Contract status", description:"Where the work stands against the approved contract: each step's state (planned, built, fuzzy, verified, failing), derived from the stages and their Checks on the latest run, with the next action, plus nodes outside every stage and the to-do list (untyped ports, missing checks, ...).",
    parameters:Type.Object({}), execute:async (_id,args,signal) => canvas("contract_status",args,signal),
  }));
  pi.registerTool(defineTool({
    name:"comfy_propose_tool", label:"Propose tool", description:"Ask the user to approve a Python tool you wrote at tools/<name>.py in this workflow's workspace. The user sees the full code; once approved, the Agent Tool node can run that exact code (stdlib only, confined, time-limited). Any later change to the code needs a new approval.",
    parameters:Type.Object({name:Type.String({pattern:"^[a-z][a-z0-9_]{0,31}$"}),description:Type.String({minLength:1,maxLength:1200}),inputs:Type.Array(Type.Object({name:Type.String({minLength:1,maxLength:80}),kind:Type.String({minLength:1,maxLength:40})}),{maxItems:16}),outputs:Type.Array(Type.Object({name:Type.String({minLength:1,maxLength:80}),kind:Type.String({minLength:1,maxLength:40})}),{maxItems:16})}),
    execute:async (_id,args,signal) => canvas("tool_propose",args,signal),
  }));
}
