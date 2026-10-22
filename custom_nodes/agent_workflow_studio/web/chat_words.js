// What the chat says while the agent works: each tool in the user's words, not its name.
const PHRASES = {
  comfy_read_workflow: 'Reading the canvas',
  comfy_node_catalog: 'Looking up nodes',
  comfy_edit_workflow: 'Editing the canvas',
  comfy_ask_user: 'Asking you',
  comfy_run_workflow: 'Running the workflow',
  comfy_execution_results: 'Reading the results',
  comfy_propose_contract: 'Proposing the contract',
  comfy_propose_design: 'Proposing the design',
  comfy_build_step: 'Building a step',
  comfy_check_gates: 'Checking each step',
  comfy_loop_step: 'Starting an improvement round',
  comfy_run_examples: 'Running the examples',
  comfy_harden_step: 'Hardening a step',
  comfy_analyze_graph: 'Analyzing the workflow',
  comfy_find_converter: 'Finding a converter',
  comfy_input_files: 'Looking at your files',
  comfy_contract_status: 'Checking progress',
  comfy_export_check: 'Checking it can be exported',
  comfy_export_python: 'Exporting as Python (install, test, package)',
  comfy_propose_tool: 'Proposing a code tool',
  comfy_workspace: 'Working with workflow files',
  read: 'Reading a file',
  write: 'Writing a file',
  edit: 'Editing a file',
};

/** A tool's name as the user reads it ("comfy_build_step" → "Building a step"). */
export function toolPhrase(name) {
  const known = PHRASES[name] ?? PHRASES[String(name).replace(/^mcp__\w+?__/, '')];
  if (known) return known;
  const words = String(name ?? 'working').replace(/^(mcp__\w+?__)?(comfy_)?/, '').replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The status line once a reply is finished, from what the turn did with the workflow. */
export function finishedText({failed, completed, submitted}) {
  return failed ? 'Done · the run failed; the error is on the canvas'
    : completed ? 'Done · the workflow ran'
    : submitted ? 'Done · the run is still going'
    : 'Done';
}
