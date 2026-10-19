"""Per-workflow git history: every agent edit and user save becomes a commit.

Each workflow gets ``<root>/<uuid>/workflow.json`` as its work tree and a git
directory beside it (``<root>/<uuid>.git``), outside anything the agent can
write. Git runs with system/global config, hooks and signing disabled, so no
file can make it execute code. Restoring an old version is a new commit;
history is never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from .pi_chat_workspace import workspace_id_for_graph
from .workflow_contract import from_plan_v1, from_yaml, render, report, to_yaml

# Pairs: same/different/unsure; customers: correct/wrong; a record in a customer: not_this_person.
REVIEW_DECISIONS = {"same", "different", "unsure", "correct", "wrong", "not_this_person"}
REVIEW_FILE_LIMIT = 20 * 1024 * 1024
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
        data = _snapshot(workflow)
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

    def show(self, sha: str) -> dict | None:
        """The workflow snapshot at a version, or None if that version has none (e.g. a plan)."""
        return self._json_at(sha, lambda name: name == "workflow.json", all_files=True)

    def diff(self, sha: str) -> dict:
        """Node and link changes the commit made relative to its parent."""
        entry = next((e for e in self.log() if e["sha"] == sha), None)
        if entry is None:
            raise ValueError("unknown commit")
        empty = {"nodes": [], "links": []}
        before = (self.show(entry["parent"]) if entry["parent"] else None) or empty
        return node_diff(before, self.show(sha) or empty)

    def _json_at(self, sha: str, wanted, *, all_files: bool = False) -> dict | None:
        found = self._file_at(sha, wanted, all_files=all_files)
        return json.loads(found[1]) if found else None

    def _file_at(self, sha: str, wanted, *, all_files: bool = False) -> tuple[str, str] | None:
        """(name, text) of a file at a version: any file it contains, or only files it changed."""
        if not SHA.fullmatch(sha):
            raise ValueError("invalid commit id")
        with self.lock:
            listing = (
                ("ls-tree", "--name-only", "-r", sha)
                if all_files
                else ("show", "--name-only", "--format=", sha)
            )
            name = next((n for n in self._git(*listing).split() if wanted(n)), None)
            return (name, self._git("show", f"{sha}:{name}")) if name else None

    def add_record(self, record: dict, message: str, workflow: dict) -> str:
        """Commit a run record under records/ together with the workflow that ran."""
        data = json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        digest = hashlib.sha256(data.encode()).hexdigest()[:12]
        name = f"records/{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{digest}.json"
        snapshot = _snapshot(workflow)
        with self.lock:
            self._ensure()
            (self.tree / "records").mkdir(exist_ok=True)
            (self.tree / name).write_text(data)
            (self.tree / "workflow.json").write_text(snapshot)
            self._git("add", name, "workflow.json")
            self._git("commit", "--quiet", "-m", " ".join(message.split())[:500], author="agent")
            return self._git("rev-parse", "HEAD").strip()

    def save_contract(self, contract: dict) -> str:
        """Commit the user-approved contract as contract.yaml (replacing a pre-contract spec.json)."""
        with self.lock:
            self._ensure()
            (self.tree / "contract.yaml").write_text(to_yaml(contract))
            self._git("add", "contract.yaml")
            if (self.tree / "spec.json").exists():
                self._git("rm", "--quiet", "spec.json")
            if not self._git("status", "--porcelain").strip():
                return self._git("rev-parse", "HEAD").strip()
            message = " ".join(f"Contract approved: {contract['purpose']}".split())[:500]
            self._git("commit", "--quiet", "-m", message, author="user")
            return self._git("rev-parse", "HEAD").strip()

    def load_contract(self) -> dict | None:
        """The latest approved contract; a plan approved before contracts existed is converted."""
        if (self.tree / "contract.yaml").is_file():
            return from_yaml((self.tree / "contract.yaml").read_text())
        if (self.tree / "spec.json").is_file():
            return from_plan_v1(json.loads((self.tree / "spec.json").read_text()))
        return None

    def save_tool(self, name: str, code: str, manifest: dict) -> str:
        """Commit a user-approved agent tool: its exact code and a manifest with the code's hash."""
        with self.lock:
            self._ensure()
            folder = self.tree / "tools"
            folder.mkdir(exist_ok=True)
            (folder / f"{name}.py").write_text(code)
            (folder / f"{name}.json").write_text(
                json.dumps(manifest, indent=1, ensure_ascii=False) + "\n"
            )
            self._git("add", f"tools/{name}.py", f"tools/{name}.json")
            if not self._git("status", "--porcelain").strip():
                return self._git("rev-parse", "HEAD").strip()
            self._git("commit", "--quiet", "-m", f"Tool approved: {name}", author="user")
            return self._git("rev-parse", "HEAD").strip()

    def load_labels(self) -> dict:
        """The user's review decisions, {item id: decision}, as saved from the Review tab."""
        path = self.tree / "labels" / "review.json"
        return json.loads(path.read_text())["labels"] if path.is_file() else {}

    def save_labels(self, decisions: dict, run: str) -> str:
        """Merge review decisions (None clears one) and commit them as the user's."""
        if not isinstance(decisions, dict) or not all(
            isinstance(k, str) and len(k) <= 200 and (v is None or v in REVIEW_DECISIONS)
            for k, v in decisions.items()
        ):
            raise ValueError(
                "Review decisions must map item ids to " + ", ".join(sorted(REVIEW_DECISIONS)) + "."
            )
        with self.lock:
            self._ensure()
            labels = {**self.load_labels(), **decisions}
            labels = {k: v for k, v in labels.items() if v is not None}
            (self.tree / "labels").mkdir(exist_ok=True)
            (self.tree / "labels" / "review.json").write_text(
                json.dumps(
                    {"run": str(run)[:200], "labels": labels},
                    indent=1,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n"
            )
            self._git("add", "labels/review.json")
            if not self._git("status", "--porcelain").strip():
                return self._git("rev-parse", "HEAD").strip()
            counts = {d: list(labels.values()).count(d) for d in sorted(set(labels.values()))}
            summary = ", ".join(f"{n} {d}" for d, n in counts.items())
            self._git(
                "commit",
                "--quiet",
                "-m",
                f"Review · {len(labels)} decisions ({summary})",
                author="user",
            )
            return self._git("rev-parse", "HEAD").strip()

    def load_tool(self, name: str) -> tuple[str, dict]:
        """The approved code of a tool; refuses if it differs from what the user approved."""
        from .agent_tools import code_hash

        folder = self.tree / "tools"
        if not (folder / f"{name}.json").is_file():
            raise ValueError(f"No approved tool named {name!r} in this workflow.")
        manifest = json.loads((folder / f"{name}.json").read_text())
        code = (folder / f"{name}.py").read_text()
        if code_hash(code) != manifest["sha256"]:
            raise ValueError(f"Tool {name!r} changed since it was approved; propose it again.")
        return code, manifest

    def record(self, sha: str) -> dict | None:
        """The run record a commit added, if any."""
        return self._json_at(
            sha, lambda name: name.startswith("records/") and name.endswith(".json")
        )

    def contract_at(self, sha: str) -> dict | None:
        """The contract (or pre-contract plan) a commit approved, if any."""
        found = self._file_at(sha, lambda name: name in ("contract.yaml", "spec.json"))
        if not found:
            return None
        name, text = found
        return from_yaml(text) if name == "contract.yaml" else from_plan_v1(json.loads(text))

    def restore(self, sha: str) -> tuple[str | None, dict]:
        workflow = self.show(sha)
        if workflow is None:
            raise ValueError("This version has no canvas snapshot to restore.")
        return self.commit(workflow, f"Restore {sha[:8]}", "user"), workflow


def _snapshot(workflow: dict) -> str:
    if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
        raise ValueError("workflow must be a serialized ComfyUI graph")
    data = json.dumps(workflow, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if len(data.encode()) > MAX_WORKFLOW_BYTES:
        raise ValueError("workflow is too large to version")
    return data


def node_diff(before: dict, after: dict) -> dict:
    # The work lives inside stages (subgraph definitions): compare their nodes and wires too.
    def definitions(graph):
        return [
            d for d in (graph.get("definitions") or {}).get("subgraphs", []) if isinstance(d, dict)
        ]

    def nodes(graph):
        found = {n["id"]: n for n in graph.get("nodes", []) if isinstance(n, dict) and "id" in n}
        for d in definitions(graph):
            for n in d.get("nodes", []):
                if isinstance(n, dict) and "id" in n:
                    key = f"{d['id']}:{n['id']}"
                    found[key] = {**n, "id": key, "stage": d.get("name")}
        return found

    def links(graph):
        # Serialized links are [id, from_node, from_slot, to_node, to_slot, type]; inside a stage, objects.
        found = {tuple(link[1:5]) for link in graph.get("links") or [] if isinstance(link, list)}
        for d in definitions(graph):
            for link in d.get("links") or []:
                if isinstance(link, dict):
                    found.add(
                        (
                            d["id"],
                            *(
                                link.get(k)
                                for k in ("origin_id", "origin_slot", "target_id", "target_slot")
                            ),
                        )
                    )
        return found

    def label(node):
        return {
            "id": node["id"],
            "type": node.get("type"),
            "title": node.get("title"),
            **({"stage": node["stage"]} if node.get("stage") else {}),
        }

    old, new = nodes(before), nodes(after)
    watched = ("type", "title", "mode", "widgets_values")
    return {
        # Sorted, so a version's diff reads the same every time.
        "added": [label(new[i]) for i in sorted(new.keys() - old.keys(), key=str)],
        "removed": [label(old[i]) for i in sorted(old.keys() - new.keys(), key=str)],
        "changed": [
            label(new[i])
            for i in sorted(new.keys() & old.keys(), key=str)
            if any(new[i].get(k) != old[i].get(k) for k in watched)
        ],
        "links_added": len(links(after) - links(before)),
        "links_removed": len(links(before) - links(after)),
    }


def _detail(repo: ProjectRepo, sha: str) -> dict:
    contract = repo.contract_at(sha)
    return {
        "workflow": repo.show(sha),
        "diff": repo.diff(sha),
        "record": repo.record(sha),
        "plan_text": render(contract) if contract else None,
    }


def projects_root() -> Path:
    import folder_paths

    return Path(folder_paths.get_user_directory()) / "agent-projects"


def install_project_routes() -> None:
    import asyncio

    from aiohttp import web
    from server import PromptServer

    from .routes import _is_local_request

    root = projects_root()
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
            request,
            lambda repo: _detail(repo, sha),
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

    @routes.get(prefix + "/contract")
    async def contract(request):
        def run(repo):
            current = repo.load_contract()
            return {"contract": current}

        return await call(request, run)

    @routes.post(prefix + "/contract/status")
    async def contract_status(request):
        try:
            evidence = await request.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(evidence, dict) or not isinstance(evidence.get("nodes"), list):
            return web.json_response({"error": "invalid evidence"}, status=400)

        def run(repo):
            current = repo.load_contract()
            return {"contract": current, **(report(current, evidence) if current else {})}

        return await call(request, run)

    @routes.get(prefix + "/review")
    async def review(request):
        def run(repo):
            import folder_paths

            runs = Path(folder_paths.get_output_directory()) / "agent_tools" / repo.id
            found = sorted(runs.glob("*/review.json"), key=lambda p: p.stat().st_mtime)
            if not found:
                return {"review": None, "labels": repo.load_labels()}
            if found[-1].stat().st_size > REVIEW_FILE_LIMIT:
                raise ValueError("review.json is over 20 MB; ask the agent to write fewer items.")
            return {
                "run": found[-1].parent.name,
                "review": json.loads(found[-1].read_text(encoding="utf-8")),
                "labels": repo.load_labels(),
            }

        return await call(request, run)

    @routes.get(prefix + "/customers")
    async def customers(request):
        def run(repo):
            import folder_paths

            runs = Path(folder_paths.get_output_directory()) / "agent_tools" / repo.id
            found = sorted(
                runs.glob("cluster_guard-*/customers.json"), key=lambda p: p.stat().st_mtime
            )
            if not found:
                return {"customers": None}
            # Searched and paged here, so the tab stays fast however many customers there are.
            everything = json.loads(found[-1].read_text(encoding="utf-8"))
            query = request.query.get("q", "").strip().lower()
            offset = max(int(request.query.get("offset", 0) or 0), 0)
            limit = min(max(int(request.query.get("limit", 40) or 40), 1), 200)
            matches = sorted(
                (
                    (cid, c)
                    for cid, c in everything.items()
                    if not query
                    or query in cid.lower()
                    or query in json.dumps(c["records"], ensure_ascii=False).lower()
                ),
                key=lambda item: -len(item[1]["records"]),
            )
            return {
                "run": found[-1].parent.name,
                "total": len(everything),
                "matches": len(matches),
                "customers": dict(matches[offset : offset + limit]),
            }

        return await call(request, run)

    @routes.post(prefix + "/review/labels")
    async def review_labels(request):
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid review request"}, status=400)
        return await call(
            request, lambda repo: {"sha": repo.save_labels(body.get("labels"), body.get("run", ""))}
        )

    @routes.post(prefix + "/restore/{sha}")
    async def restore(request):
        sha = request.match_info["sha"]

        def run(repo):
            new_sha, workflow = repo.restore(sha)
            return {"sha": new_sha, "workflow": workflow}

        return await call(request, run)


__all__ = ["ProjectRepo", "install_project_routes", "node_diff"]
