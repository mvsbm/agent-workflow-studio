"""The studio's logic without ComfyUI: plain functions a node wraps and an exported repo calls.

Modules here import only the standard library, numpy, scipy and PyYAML (never comfy_api,
folder_paths or the rest of the pack), so "Export as Python" can copy this folder as it is.
tests/unit/test_core_isolation.py imports it with ComfyUI off the path.
"""
