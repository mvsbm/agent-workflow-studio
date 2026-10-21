"""The studio's defaults for ComfyUI's own settings: this is a data and text studio, so ComfyUI's image,
3D and audio editors are switched off (its native "Comfy.Extension.Disabled" setting, so Settings →
Extension turns any of them back on), and the node source badge, which here would only repeat this
pack's folder name, is hidden. Filled in at startup only where the user has not set them, so a choice
the user makes always stays.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

# ComfyUI frontend extensions that only edit or preview images, 3D models or audio.
MEDIA_EXTENSIONS = [
    "Comfy.AudioWidget",
    "Comfy.Clipspace",
    "Comfy.CreateBoundingBoxes",
    "Comfy.CreateCameraInfo",
    "Comfy.ImageCompare",
    "Comfy.ImageCompositor",
    "Comfy.ImageCrop",
    "Comfy.LayerEditor",
    "Comfy.Load3D",
    "Comfy.Load3DAdvanced",
    "Comfy.Load3DLazy",
    "Comfy.MaskEditor",
    "Comfy.Painter",
    "Comfy.Preview3D",
    "Comfy.RecordAudio",
    "Comfy.SaveGLB",
    "Comfy.SaveImageExtraOutput",
    "Comfy.UploadAudio",
    "Comfy.UploadImage",
    "Comfy.WebcamCapture",
]
DEFAULTS = {
    "Comfy.Extension.Disabled": MEDIA_EXTENSIONS,
    "Comfy.NodeBadge.NodeSourceBadgeMode": "None",
}


def apply(settings_file: Path) -> list[str]:
    """Fill in the defaults the user has not set; returns the ones filled in."""
    try:
        settings = (
            json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else {}
        )
    except (OSError, ValueError):
        return []  # an unreadable settings file is left for ComfyUI to report, never overwritten
    missing = {key: value for key, value in DEFAULTS.items() if key not in settings}
    if missing:
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps({**settings, **missing}, indent=4), encoding="utf-8")
    return sorted(missing)


def apply_to_default_user() -> None:
    import folder_paths

    filled = apply(Path(folder_paths.get_user_directory()) / "default" / "comfy.settings.json")
    if filled:
        logging.info("Agent Workflow Studio: set ComfyUI defaults %s", ", ".join(filled))
