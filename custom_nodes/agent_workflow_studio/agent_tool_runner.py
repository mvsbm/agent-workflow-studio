"""Runs one agent tool: reads JSON inputs on stdin, calls run(inputs), prints JSON outputs.

Executed by agent_tools.run_tool in a separate (sandboxed) process; stdlib only.
"""

import importlib.util
import json
import sys
import traceback


def main() -> int:
    try:
        inputs = json.loads(sys.stdin.read() or "{}")
        spec = importlib.util.spec_from_file_location("agent_tool", sys.argv[1])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        outputs = module.run(inputs)
        if not isinstance(outputs, dict):
            raise TypeError("run(inputs) must return a dict")
        print(json.dumps({"outputs": outputs}, ensure_ascii=False))
        return 0
    except Exception as error:  # report the tool's own failure, not the runner's
        detail = "".join(traceback.format_exception_only(type(error), error)).strip()
        # Where in the tool it failed: a bare "AssertionError" is useless for debugging.
        frames = [f for f in traceback.extract_tb(error.__traceback__) if f.filename == sys.argv[1]]
        if frames:
            last = frames[-1]
            detail += (
                f" (tool.py line {last.lineno}, in {last.name}: {(last.line or '').strip()[:200]})"
            )
        print(json.dumps({"error": detail[-800:]}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
