"""Unit tests of the agent_workflow_studio package (run by scripts/test.sh from the ComfyUI root)."""

# Tests that import ComfyUI's server load its model management, which picks a GPU at import: run on
# the CPU, as the end-to-end fixture does with --cpu, so CI machines without CUDA behave like laptops.
from comfy.cli_args import args

args.cpu = True
