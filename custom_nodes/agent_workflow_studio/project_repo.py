"""Per-workflow git history: every agent edit and user save becomes a commit.

Each workflow gets ``<root>/<uuid>/workflow.json`` as its work tree and a git
directory beside it (``<root>/<uuid>.git``), outside anything the agent can
write. Git runs with system/global config, hooks and signing disabled, so no
file can make it execute code. Restoring an old version is a new commit;
history is never rewritten.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path

from .pi_chat_workspace import workspace_id_for_graph

AUTHORS = {
    "agent": ("Pi agent", "agent@agent-workflow-studio"),
    "user": ("User", "user@agent-workflow-studio"),
}
MAX_WORKFLOW_BYTES = 8 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


class ProjectRepo:
    def __init__(self, root: Path, workflow_id: str) -> None:
        self.id = workspace_id_for_graph(workflow_id)
        self.tree = Path(root) / self.id
        self.git_dir = Path(root) / f"{self.id}.git"
        with _locks_guard:
            self.lock = _locks.setdefault(self.git_dir, threading.Lock())

    def _git(self, *args: str, author: str | None = None) -> str:
        name, email = AUTHORS[author or "user"]
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.tree.parent),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
        command = ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.tree}"]
        command += ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args]
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
        if result.returncode:
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
        return result.stdout

    def _ensure(self) -> None:
        if not self.git_dir.exists():
            self.tree.mkdir(parents=True, exist_ok=True)
            self._git("init", "--quiet")

    def commit(self, workflow: dict, message: str, author: str) -> str | None:
        """Commit a workflow snapshot; returns the new sha, or None if nothing changed."""
        if author not in AUTHORS:
            raise ValueError("author must be 'agent' or 'user'")
        if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
            raise ValueError("workflow must be a serialized ComfyUI graph")
        data = json.dumps(workflow, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        if len(data.encode()) > MAX_WORKFLOW_BYTES:
            raise ValueError("workflow is too large to version")
        message = " ".join(str(message).split())[:500] or "Update workflow"
        with self.lock:
            self._ensure()
            (self.tree / "workflow.json").write_text(data)
            self._git("add", "workflow.json")
            if not self._git("status", "--porcelain").strip():
                return None
            self._git("commit", "--quiet", "-m", message, author=author)
            return self._git("rev-parse", "HEAD").strip()

    def log(self, limit: int = 200) -> list[dict]:
        if not self.git_dir.exists():
            return []
        with self.lock:
            try:
                out = self._git("log", f"-n{limit}", "--format=%H%x1f%P%x1f%an%x1f%at%x1f%s")
            except RuntimeError:  # an initialized repo with no commits yet
                return []
        entries = []
        for line in out.splitlines():
            sha, parents, name, timestamp, subject = line.split("\x1f")
            author = next((k for k, (n, _) in AUTHORS.items() if n == name), "user")
            entries.append(
                {
                    "sha": sha,
                    "parent": parents.split(" ")[0] or None,
                    "author": author,
                    "time": int(timestamp),
                    "message": subject,
                }
            )
        return entries

    def show(self, sha: str) -> dict:
        if not SHA.fullmatch(sha):
            raise ValueError("invalid commit id")
        with self.lock:
            return json.loads(self._git("show", f"{sha}:workflow.json"))

    def diff(self, sha: str) -> dict:
        """Node and link changes the commit made relative to its parent."""
        entry = next((e for e in self.log() if e["sha"] == sha), None)
        if entry is None:
            raise ValueError("unknown commit")
        after = self.show(sha)
        before = self.show(entry["parent"]) if entry["parent"] else {"nodes": [], "links": []}
        return node_diff(before, after)

    def restore(self, sha: str) -> tuple[str | None, dict]:
        workflow = self.show(sha)
        return self.commit(workflow, f"Restore {sha[:8]}", "user"), workflow


def node_diff(before: dict, after: dict) -> dict:
    def nodes(graph):
        return {n["id"]: n for n in graph.get("nodes", []) if isinstance(n, dict) and "id" in n}

    def links(graph):
        # Serialized links are [id, from_node, from_slot, to_node, to_slot, type].
        return {tuple(link[1:5]) for link in graph.get("links") or [] if isinstance(link, list)}

    def label(node):
        return {"id": node["id"], "type": node.get("type"), "title": node.get("title")}

    old, new = nodes(before), nodes(after)
    watched = ("type", "title", "mode", "widgets_values")
    return {
        "added": [label(new[i]) for i in new.keys() - old.keys()],
        "removed": [label(old[i]) for i in old.keys() - new.keys()],
        "changed": [
            label(new[i])
            for i in new.keys() & old.keys()
            if any(new[i].get(k) != old[i].get(k) for k in watched)
        ],
        "links_added": len(links(after) - links(before)),
        "links_removed": len(links(before) - links(after)),
    }


def install_project_routes() -> None:
    import asyncio

    import folder_paths
    from aiohttp import web
    from server import PromptServer

    from .routes import _is_local_request

    root = Path(folder_paths.get_user_directory()) / "agent-projects"
    prefix = "/agent-workflow-studio/projects/{workflow_id}"
    routes = PromptServer.instance.routes

    async def call(request, method):
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            repo = ProjectRepo(root, request.match_info["workflow_id"])
            return web.json_response(await asyncio.to_thread(method, repo))
        except (ValueError, RuntimeError, json.JSONDecodeError) as error:
            return web.json_response({"error": str(error)}, status=400)

    @routes.get(prefix + "/log")
    async def log(request):
        return await call(request, lambda repo: {"commits": repo.log()})

    @routes.get(prefix + "/commits/{sha}")
    async def commit_detail(request):
        sha = request.match_info["sha"]
        return await call(
            request, lambda repo: {"workflow": repo.show(sha), "diff": repo.diff(sha)}
        )

    @routes.post(prefix + "/commits")
    async def commit(request):
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid commit request"}, status=400)
        return await call(
            request,
            lambda repo: {
                "sha": repo.commit(
                    body.get("workflow"), body.get("message", ""), body.get("author")
                )
            },
        )

    @routes.post(prefix + "/restore/{sha}")
    async def restore(request):
        sha = request.match_info["sha"]

        def run(repo):
            new_sha, workflow = repo.restore(sha)
            return {"sha": new_sha, "workflow": workflow}

        return await call(request, run)


__all__ = ["ProjectRepo", "install_project_routes", "node_diff"]
