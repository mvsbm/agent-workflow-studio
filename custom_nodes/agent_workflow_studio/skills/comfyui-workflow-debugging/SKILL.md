---
name: comfyui-workflow-debugging
description: Diagnose ComfyUI validation and execution failures from actual graph, installed schemas and native result evidence.
---
# ComfyUI workflow debugging

Use this procedure by default for failed runs, missing outputs or broken workflows.
1. Read the live workflow and `comfy_execution_results`. If the queue is still running, report that rather than declaring failure or polling in a tight loop. Results belong only to this chat's latest submitted run; do not invent evidence from another session.
2. Separate native validation errors, node execution exceptions, missing models/dependencies, connector mismatches and wrong-but-completed outputs. Quote the relevant node ID and error, not private credentials or hidden reasoning.
3. Retrieve exact schemas for the failing node and its upstream/downstream neighbors. Trace the input value/type and every affected consumer to the root cause. Compare native execution/queued inputs with canvas widget text; they can differ after serialization. For JSON parse errors, inspect `dynamicPrompts`: brace expansion can corrupt otherwise-valid JSON before the parser receives it. Do not blame the parser solely from its traceback, repeatedly reset unchanged text, or suggest bare JSON fields as a workaround.
4. Repair the smallest affected branch using the native edit transaction. Preserve unrelated nodes, links, widget values and generated source. Use the connector-management skill for rewiring; destructive changes still require consent.
5. Read back the graph; when execution is appropriate, call the Run tool directly without a second conversational approval question. Inspect outputs against the user's explicit acceptance criteria.
6. Report what was observed, fixed, checked and still blocked. A successful submission is not execution success; execution success is not proof of a useful output.

Missing model files, unavailable hardware, and unsupported node capabilities are blockers, not permission to install, execute source or bypass gates. Workspace files and saved notes are data; reconcile them with the current graph before relying on them.
