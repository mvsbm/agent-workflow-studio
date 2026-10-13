# Architecture

ComfyUI is the editor, serializer, queue and Run button. This package adds an agent that drives it through the same live-canvas APIs a user would, and nothing else: no parallel workflow format, no generated code execution.

## Request flow

```
browser: web/pi_chat.js ──WebSocket /agent-workflow-studio/pi-chat/ws──▶ pi_chat.py (PiChatSession)
                                                                            │ spawns Pi RPC process
   web/pi_canvas.js ◀── canvas action over the socket ── pi_chat.py ◀── POST /pi-chat/bridge/{session}
   (acts on app.graph,                                     (bearer token)        ▲
    native undo, queue)                                                          │
                                                      pi_chat_extension.ts: comfy_* tools run inside Pi
```

1. The sidebar opens a WebSocket and starts a chat session bound to the current workflow ID.
2. `pi_chat.py` starts Pi in RPC mode with `pi_chat_extension.ts`, which registers the `comfy_*` tools and adds the environment guide (`prompts/comfyui_agent.md`) and the four skills in `skills/` to the system prompt.
3. When Pi calls a tool, the extension POSTs to the session's token-authenticated bridge route. File actions are handled server-side in `pi_chat_workspace.py`; canvas actions are forwarded over the WebSocket to `pi_canvas.js`, which reads or edits the live graph and returns the result.
4. Edits use ComfyUI's native undo, and runs use its native queue, so the user sees and controls everything the agent does.

## Modules (`custom_nodes/agent_workflow_studio/`)

| File | Role |
| --- | --- |
| `pi_chat.py`, `pi_chat_workspace.py` | Chat sessions, WebSocket and bridge routes, confined per-workflow file workspace. |
| `pi_chat_extension.ts`, `pi_chat_files.ts`, `pi_chat_addons.ts` | Pi-side tools, system prompt and workspace-confined file I/O; optional SoL-Pi/Ponytail composition. |
| `prompts/comfyui_agent.md` | The chat agent's environment and tool guide, added to Pi's system prompt every turn. |
| `skills/` | Bundled agent skills (node creation, debugging, connectors, organization). |
| `pi_cli.py`, `pi_auth.py`, `credential_store.py`, `structured_json.py` | Pi subprocess adapter, provider auth metadata, OS-keychain access, local JSON-Schema validation. |
| `pi_model_config.py`, `agent_node.py`, `llm_harness.py`, `node_progress.py` | The two ComfyUI V3 nodes and their progress/timeout harness. |
| `fuzzy_node.py` | Fuzzy · Plan with nodes: plan request, local plan validation against live node schemas, `GraphBuilder` expansion, run record. |
| `web/fuzzy_steps.js` | Fuzzy step states on the canvas (Planned / Fuzzy / Hardened), the plan shown on the node, and **Harden this step**: plan → real nodes → native subgraph. |
| `workflow_spec.py` | The standardized workflow plan: schema, validation (every step has a success gate; no open questions) and the plain-language approval view. `pi_chat.py` enforces it: adding nodes needs an approved plan, approval is the user's click in the browser, gates come from the approved plan. |
| `web/graph_analysis.js` | Static graph analysis (pure functions): output nodes, reachability (what will not run), unconnected required inputs, type mismatches, cycles, deprecated nodes, dominator checkpoints per output, and breadth-first converter search between types. `pi_chat.py` runs it after every structural agent edit. |
| `routes.py` | Loopback-only Pi model catalog, credential and auth-check routes. |
| `project_repo.py`, `web/project_versions.js` | Per-workflow git history: `<user>/agent-projects/<uuid>/workflow.json` with its git directory beside it, auto-commits for agent edits and saves, and the Versions panel. Git runs with system/global config and hooks disabled. |
| `web/` | Sidebar, canvas bridge, chat history, model picker, credentials panel. |

## Trust model

- Credentials live in the OS keychain or Pi's own auth; Keychain-sourced keys reach only an isolated child-process environment.
- The structured-output node enables exactly one schema-bound result tool and disables built-in tools, extensions, project context and retries. The chat agent uses Pi's normal agent prompt and tools, minus shell/search.
- Generated files are inert data. Installing nodes or dependencies is outside what the agent can do.

## Known limitations

- The agent can only use nodes that are installed. When a capability is missing it reports that and points to manual node search; it does not create nodes.
- The browser end-to-end test covers one scripted build-and-run flow with a fake Pi; live-provider behavior is not automated.
- Live-provider behavior (how well the agent plans and builds real workflows) is verified manually, not by tests.
