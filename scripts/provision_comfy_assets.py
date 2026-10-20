"""Create the empty optional assets ComfyUI's frontend requests, without overwriting user files."""

from __future__ import annotations

import argparse
from pathlib import Path


def provision_optional_assets(user: Path, static: Path) -> list[str]:
    """Supply absent optional customization assets without replacing user content."""
    if not static.is_dir():
        raise ValueError("frontend_static_root_missing")
    defaults = user / "default"
    for name in ("workflows", "subgraphs"):
        (defaults / name).mkdir(parents=True, exist_ok=True)
    added = []
    for path, value in (
        (static / "user.css", ""),
        (defaults / "user.css", ""),
        (defaults / "comfy.templates.json", "[]"),
    ):
        try:
            with path.open("x") as stream:
                stream.write(value)
        except FileExistsError:
            continue
        added.append(str(path))
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-root", type=Path, required=True)
    parser.add_argument("--frontend-root", type=Path, required=True)
    args = parser.parse_args()
    provision_optional_assets(args.user_root, args.frontend_root)


if __name__ == "__main__":
    main()
