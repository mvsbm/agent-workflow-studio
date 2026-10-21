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

from .native_layers import layers
from .pi_chat_workspace import workspace_id_for_graph
from .workflow_contract import (
    present,
    render,
    report,
    title_of,
    validate_contract,
)

# Pairs: same/different/unsure; groups: correct/wrong; a record in a group: not_this_person.
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
                    **layer_of(subject, author),
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

    def add_record(self, record: dict, message: str, workflow: dict, author: str = "agent") -> str:
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
            self._git("commit", "--quiet", "-m", " ".join(message.split())[:500], author=author)
            return self._git("rev-parse", "HEAD").strip()

    def commit_contract(self, workflow: dict, message: str) -> str:
        """Commit the workflow holding a just-approved contract or design natively, as the user's."""
        with self.lock:
            self._ensure()
            (self.tree / "workflow.json").write_text(_snapshot(workflow))
            self._git("add", "workflow.json")
            message = " ".join(message.split())[:500]
            self._git("commit", "--quiet", "--allow-empty", "-m", message, author="user")
            return self._git("rev-parse", "HEAD").strip()

    def adopt_template(self, workflow: dict) -> bool:
        """A workflow opened with its contract inside (a template) becomes its own first version,
        once: only while it has no history (so it never overrides what a user or agent did since)."""
        if self.log():
            return False
        contract = validate_contract(present(layers(workflow)))
        with self.lock:
            self._ensure()
            (self.tree / "workflow.json").write_text(_snapshot(workflow))
            self._git("add", "workflow.json")
            message = f"Template: {contract['purpose']} (contract shipped with the workflow)"
            self._git("commit", "--quiet", "-m", " ".join(message.split())[:500], author="agent")
        return True

    def load_contract(self) -> dict | None:
        """The contract the latest version holds natively, if any."""
        if (self.tree / "workflow.json").is_file():
            return layers(json.loads((self.tree / "workflow.json").read_text()))
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

    def code_diff(self, sha: str) -> str:
        """The workflow code (tools/) a version changed, as a unified diff ("" when none)."""
        # The code first, then its manifest (ports, examples): the reader wants the source.
        show = lambda spec: self._git("show", "--format=", "--no-color", sha, "--", spec)  # noqa: E731
        return (show(":(glob)tools/*.py") + show(":(glob)tools/*.json"))[:20000]

    def tool_detail(self, name: str) -> dict:
        """One tool as the Design tab shows it: its spec and code, the version that approved it,
        and its tests' results from the latest Run checks."""
        code, manifest = self.load_tool(name)
        approved = next((e for e in self.log() if e["message"] == f"Tool approved: {name}"), None)
        runs = [r for r in self.records("pipeline") if r.get("tools") is not None]
        tests = next((t for t in (runs[-1]["tools"] if runs else []) if t["tool"] == name), None)
        return {
            "name": name,
            "manifest": manifest,
            "code": code,
            "approved": approved,
            "tests": tests,
        }

    def tool_names(self) -> set[str]:
        """The tools approved in this workflow."""
        return {p.stem for p in (self.tree / "tools").glob("*.json")}

    def load_tool(self, name: str) -> tuple[str, dict]:
        """The approved code of a tool; refuses if it differs from what the user approved."""
        from .agent_tools import NAME, code_hash

        if not NAME.fullmatch(name or ""):
            raise ValueError("unknown tool")
        folder = self.tree / "tools"
        if not (folder / f"{name}.json").is_file():
            raise ValueError(f"No approved tool named {name!r} in this workflow.")
        manifest = json.loads((folder / f"{name}.json").read_text())
        code = (folder / f"{name}.py").read_text()
        if code_hash(code) != manifest["sha256"]:
            raise ValueError(f"Tool {name!r} changed since it was approved; propose it again.")
        return code, manifest

    def records(self, kind: str) -> list[dict]:
        """Committed records of one kind (loop iterations, acceptance runs, …), oldest first."""
        folder = self.tree / "records"
        found = []
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("kind") == kind:
                found.append(record)
        return found

    def head(self) -> str | None:
        log = self.log(1)
        return log[0]["sha"] if log else None

    def record(self, sha: str) -> dict | None:
        """The run record a commit added, if any."""
        return self._json_at(
            sha, lambda name: name.startswith("records/") and name.endswith(".json")
        )

    def contract_at(self, sha: str) -> dict | None:
        """The contract a version introduced or changed, if any."""
        entry = next((e for e in self.log() if e["sha"] == sha), None)
        now = layers(self.show(sha))
        before = layers(self.show(entry["parent"])) if entry and entry["parent"] else None
        return now if now and now != before else None

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


# What a version changed, by its message: the layer of the workflow it belongs to.
LAYERS = (
    ("Contract approved", "contract", "Contract"),
    ("Template:", "contract", "Contract"),
    ("Design approved", "design", "Design (HLD)"),
    ("Checks ·", "checks", "Checks"),
    ("Acceptance ·", "checks", "Examples"),
    ("Publish API", "release", "Release"),
    ("Roll back API", "release", "Release"),
    ("Run ·", "run", "Run"),
    ("Watch ", "run", "Watch"),
    ("Loop ", "loop", "Improve loop"),
    ("Tool approved", "tool", "Agent tool"),
    ("Review", "review", "Your review"),
    ("Restore", "restore", "Restore"),
    ("Save ", "save", "Saved"),
)


def layer_of(message: str, author: str) -> dict:
    """The layer a version belongs to: contract, design, a stage's LLD, checks, run, release, …"""
    for prefix, key, label in LAYERS:
        if message.startswith(prefix):
            return {"layer": key, "layer_label": label}
    return (
        {"layer": "lld", "layer_label": "Stage build (LLD)"}
        if author == "agent"
        else {"layer": "edit", "layer_label": "Your change"}
    )


def canvas_order(title: object) -> tuple:
    """Stage titles in canvas order: "2 · Clean" before "10 · Publish"."""
    m = re.match(r"\d+", str(title))
    return (int(m[0]) if m else float("inf"), str(title))


def touched(diff: dict, workflow: dict | None) -> list[dict]:
    """The stages a version changed inside, and the requests each serves (from that version)."""
    names = {
        n.get("stage") for k in ("added", "removed", "changed") for n in diff[k] if n.get("stage")
    }
    contract = layers(workflow) if workflow else None
    requests = {r["id"]: r for r in (contract or {}).get("requests", [])}
    steps = {s["id"]: s for s in (contract or {}).get("steps", [])}
    titles = {}
    for node in (workflow or {}).get("nodes") or []:
        step = (node.get("properties") or {}).get("aws_stage")
        definition = next(
            (
                d
                for d in (workflow.get("definitions") or {}).get("subgraphs", [])
                if d.get("id") == node.get("type")
            ),
            {},
        )
        if step:
            titles[definition.get("name") or node.get("title")] = step
    out = []
    for name in sorted(names, key=canvas_order):
        step = steps.get(titles.get(name), {})
        serves = [
            f"{rid} · {title_of(requests[rid])}"
            for rid in step.get("covers", [])
            if rid in requests
        ]
        out.append({"stage": name, "step": titles.get(name), "serves": serves})
    return out


def _detail(repo: ProjectRepo, sha: str) -> dict:
    contract = repo.contract_at(sha)
    workflow, diff = repo.show(sha), repo.diff(sha)
    return {
        "workflow": workflow,
        "diff": diff,
        "touched": touched(diff, workflow),
        "record": repo.record(sha),
        "plan_text": render(contract) if contract else None,
        "code": repo.code_diff(sha) or None,
    }


def merged_review(found: list[Path]) -> dict:
    """What to review across the workflow: the newest review of each stage that asks (each Decision
    rules stage writes its own), every item tagged with the stage that sent it."""
    newest: dict[str, tuple[Path, dict]] = {}
    for path in found:  # oldest first: a later run of the same stage replaces an earlier one
        review = json.loads(path.read_text(encoding="utf-8"))
        stage = review.get("stage") or {"title": path.parent.name.rsplit("-", 2)[0]}
        newest[stage.get("id") or stage["title"]] = (path, {**review, "stage": stage})
    sections, pairs, groups = [], [], []
    for _, review in sorted(
        newest.values(), key=lambda item: canvas_order(item[1]["stage"]["title"])
    ):
        stage = review["stage"]
        pairs += [{**p, "stage": stage} for p in review.get("pairs") or []]
        groups += [{**g, "stage": stage} for g in review.get("groups") or []]
        sections.append(
            {
                "stage": stage,
                "title": review.get("title", ""),
                "pairs": len(review.get("pairs") or []),
            }
        )
    title = " · ".join(s["title"] for s in sections if s["title"]) or "Review"
    return {"title": title, "sections": sections, "pairs": pairs, "groups": groups}


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

    @routes.get(prefix + "/tools/{name}")
    async def tool_detail(request):
        name = request.match_info["name"]
        return await call(request, lambda repo: repo.tool_detail(name))

    @routes.get(prefix + "/tools/{name}/graduation")
    async def graduation_brief(request):
        """The brief for turning an approved tool into a reviewed studio node (Markdown)."""
        from .graduation import brief, stages_running

        name = request.match_info["name"]
        return await call(
            request,
            lambda repo: {
                "brief": brief(
                    name,
                    *repo.load_tool(name),
                    repo.id,
                    stages_running(repo.show(repo.head()) if repo.head() else None, name),
                )
            },
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
            from .workflow_contract import hld, review

            current = present(repo.load_contract())
            if not current:
                return {"contract": None}
            # The design as sentences and its review, computed once here for every reader.
            return {
                "contract": current,
                "design": hld(current),
                "review": [{"ok": ok, "item": item} for ok, item in review(current)],
            }

        return await call(request, run)

    @routes.post(prefix + "/template")
    async def template(request):
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict) or not isinstance(body.get("workflow"), dict):
            return web.json_response({"error": "workflow required"}, status=400)
        return await call(request, lambda repo: {"adopted": repo.adopt_template(body["workflow"])})

    @routes.post(prefix + "/contract/status")
    async def contract_status(request):
        try:
            evidence = await request.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(evidence, dict) or not isinstance(evidence.get("nodes"), list):
            return web.json_response({"error": "invalid evidence"}, status=400)

        def run(repo):
            # The live canvas holds the contract (native layers); the last version if it has none.
            current = layers(evidence.get("workflow")) or repo.load_contract()
            accepted = repo.records("acceptance") if current else []
            status = (
                report(current, evidence, accepted[-1] if accepted else None) if current else {}
            )
            return {"contract": present(current), **status}

        return await call(request, run)

    @routes.get(prefix + "/review")
    async def review(request):
        def run(repo):
            import folder_paths

            runs = Path(folder_paths.get_output_directory()) / "agent_tools" / repo.id
            found = sorted(runs.glob("*/review.json"), key=lambda p: p.stat().st_mtime)
            if not found:
                return {"review": None, "labels": repo.load_labels()}
            if any(p.stat().st_size > REVIEW_FILE_LIMIT for p in found[-12:]):
                raise ValueError("review.json is over 20 MB; ask the agent to write fewer items.")
            return {
                "run": found[-1].parent.name,
                "review": merged_review(found),
                "labels": repo.load_labels(),
            }

        return await call(request, run)

    @routes.get(prefix + "/loop")
    async def loop_journal(request):
        def run(repo):
            from .loop import budget_of, outside_loop

            return {
                "journal": repo.records("loop"),
                "budget": budget_of(repo.load_contract()),
                "outside": outside_loop(repo.log()),
            }

        return await call(request, run)

    @routes.get("/agent-workflow-studio/follow")
    async def follow_record(request):
        if not _is_local_request(request):  # customer records: this machine only
            return web.json_response({"error": "local access only"}, status=403)
        from functools import partial

        from .table_nodes import follow

        query = request.query.get("q", "").strip()
        if not query:
            return web.json_response({"error": "q is required"}, status=400)
        found = await asyncio.to_thread(follow, query)
        return web.json_response({"steps": found}, dumps=partial(json.dumps, default=str))

    @routes.get(prefix + "/clusters")
    async def clusters(request):
        def run(repo):
            import folder_paths

            runs = Path(folder_paths.get_output_directory()) / "agent_tools" / repo.id
            # The latest Cluster guard run's explained clusters (customers.json before the standard).
            found = sorted(
                [
                    *runs.glob("cluster_guard-*/clusters.json"),
                    *runs.glob("cluster_guard-*/customers.json"),
                ],
                key=lambda p: p.stat().st_mtime,
            )
            if not found:
                return {"clusters": None}
            # Searched and paged here, so the tab stays fast however many clusters there are.
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
                "clusters": dict(matches[offset : offset + limit]),
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
