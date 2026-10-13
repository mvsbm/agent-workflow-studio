"""Send private ComfyUI per-node progress text to the active client."""

from __future__ import annotations


def node_id_for(node_class: type) -> str:
    hidden = getattr(node_class, "hidden", None)
    return getattr(hidden, "unique_id", "") or ""


def report_node_progress(node_id: str, text: str) -> None:
    if not node_id:
        return
    try:
        from server import PromptServer

        server = PromptServer.instance
        client_id = server.client_id
        if client_id is not None:
            server.send_progress_text(text, node_id, sid=client_id)
    except Exception:
        return


__all__ = ["node_id_for", "report_node_progress"]
