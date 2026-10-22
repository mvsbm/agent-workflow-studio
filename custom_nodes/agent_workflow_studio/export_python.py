"""Export as Python (R41): a published, green workflow becomes a small repo with a command line.

Deterministic, never written by a model: the release's graph (the API prompt it was published with)
is laid out as calls to the core steps its nodes wrap (EXPORT = "module:function" on a node class),
the core is copied as it is, the App inputs become options and a config file, the contract's
examples become the tests, and the review step becomes a CSV round trip. Then the repo is installed
in a fresh environment, its `check` command run, and it is packaged (wheel, sdist, zip).
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import keyword
import pprint
import re
import shutil
import subprocess
import sys
import zipfile
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).parent
CORE = HERE / "core"
# ComfyUI's own nodes that only show something: an export leaves them out.
UI_ONLY = {"PreviewAny", "Note", "MarkdownNote", "PrimitiveNode"}
DEPENDENCIES = {"numpy": "numpy", "scipy": "scipy", "yaml": "pyyaml"}


def pack_nodes() -> dict[str, type]:
    """The studio's node classes by node id (what a prompt's class_type names)."""
    from .agent_node import PiStructuredAgent
    from .agent_tool_node import AgentToolNode
    from .check_node import NODES as CHECK
    from .contacts_nodes import NODES as CONTACTS
    from .decision_rules import DecisionRules
    from .eda_node import NODES as EDA
    from .fuzzy_node import FuzzyNode
    from .matching_nodes import NODES as MATCHING
    from .nayose_nodes import NODES as NAYOSE
    from .pi_model_config import AgentWorkflowPiModelConfig
    from .semantic_nodes import NODES as SEMANTIC
    from .synth_nodes import NODES as SYNTH
    from .table_calc import NODES as CALC
    from .table_nodes import NODES as TABLE

    classes = [
        AgentWorkflowPiModelConfig,
        PiStructuredAgent,
        FuzzyNode,
        AgentToolNode,
        DecisionRules,
    ]
    classes += [*CHECK, *CONTACTS, *EDA, *MATCHING, *NAYOSE, *SEMANTIC, *SYNTH, *CALC, *TABLE]
    return {cls.define_schema().node_id: cls for cls in classes}


def slug(text: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "workflow"
    return f"w_{name}" if name[0].isdigit() or keyword.iskeyword(name) else name


def var(node_id: str) -> str:
    return "n" + re.sub(r"[^0-9A-Za-z]+", "_", str(node_id))


def title_of(release: dict, node_id: str) -> str:
    node = release["prompt"][node_id]
    return (node.get("_meta") or {}).get("title") or node["class_type"]


# --- 1 readiness ----------------------------------------------------------------------------------
def readiness(release: dict | None, contract: dict | None, current_hash: str = "") -> dict:
    """Can this release run as plain Python? Blockers stop the export; to-dos are said."""
    if not release:
        return {
            "ready": False,
            "blockers": [
                "Not published yet: run the checks (Contract tab → Run checks), then Publish."
            ],
            "todo": [],
        }
    nodes, blockers, todo = pack_nodes(), [], []
    for node_id, node in release["prompt"].items():
        kind = node["class_type"]
        cls = nodes.get(kind)
        if kind in UI_ONLY or (cls is not None and "EXPORT" in vars(cls) and cls.EXPORT is None):
            continue
        if cls is None or not getattr(cls, "EXPORT", None):
            blockers.append(
                f"{title_of(release, node_id)} ({kind}) cannot run outside ComfyUI yet."
            )
    if not (contract or {}).get("examples"):
        todo.append(
            "The contract has no examples: the exported tests will run the stage Checks only."
        )
    about = descriptions(release)
    todo += [
        f"Input {n} has no description: the command line shows only its name."
        for n, text in about.items()
        if not text
    ]
    from .workflow_api import prompt_hash

    if current_hash and current_hash != prompt_hash(release["prompt"]):
        todo.append(
            f"The canvas changed since release {release['semver']}: publish again to export the changes."
        )
    return {"ready": not blockers, "blockers": blockers, "todo": todo, "release": release["semver"]}


# --- 2 generate -----------------------------------------------------------------------------------
def order(prompt: dict) -> list[str]:
    """The nodes in an order where every node comes after the nodes it reads from."""
    needs = {
        n: {
            str(v[0])
            for v in node["inputs"].values()
            if isinstance(v, list) and len(v) == 2 and str(v[0]) in prompt
        }
        for n, node in prompt.items()
    }
    done, out = set(), []
    while len(out) < len(needs):
        ready = sorted(n for n in needs if n not in done and needs[n] <= done)
        if not ready:
            raise ValueError("The workflow has a cycle: it cannot be laid out as a program.")
        out += ready
        done |= set(ready)
    return out


def descriptions(release: dict) -> dict[str, str]:
    """Each App input's description (App mode's label for it), by input name."""
    config = {
        f"{i[0]}/{i[1]}": (i[2] or {}).get("description", "")
        for i in (release["workflow"].get("extra", {}).get("linearData", {}).get("inputs") or [])
        if len(i) > 2
    }
    return {
        item["name"]: config.get(f"{str(item['node']).split(':')[0]}/{item.get('widget')}", "")
        or config.get(f"{item.get('node')}/{item.get('widget')}", "")
        for item in release["signature"]["inputs"]
    }


def generate(release: dict, contract: dict | None, target: Path, input_dir: Path) -> Path:
    """Write the repo into `target` (replaced). Returns its path."""
    nodes, prompt = pack_nodes(), release["prompt"]
    name, pkg = release["name"], slug(release["name"])
    contract = contract or {}
    if target.exists():
        shutil.rmtree(target)
    package = target / pkg
    shutil.copytree(CORE, package / "core", ignore=shutil.ignore_patterns("__pycache__"))
    (package / "__init__.py").write_text(
        f'"""{contract.get("title") or name}, exported from Agent Workflow Studio."""\n',
        encoding="utf-8",
    )
    bound = {}  # (node, widget) -> input name
    folders = set()  # inputs that are a data folder
    for item in release["signature"]["inputs"]:
        for path, widget in item.get("targets") or [[item["node"], item["widget"]]]:
            bound[(str(path), widget)] = item["name"]
            if prompt.get(str(path), {}).get("class_type") == "AgentWorkflowDataFolder":
                folders.add(item["name"])
    if (
        len(folders) > 1
    ):  # shortcut: one data folder per workflow; upgrade when a workflow takes two
        raise ValueError("This export handles one data folder input; this workflow has several.")
    lines, bundled = [], {}
    for node_id in order(prompt):
        node = prompt[node_id]
        cls = nodes.get(node["class_type"])
        if node["class_type"] in UI_ONLY or not getattr(cls, "EXPORT", None):
            continue
        module, function = cls.EXPORT.split(":")
        accepts = inspect.signature(
            getattr(importlib.import_module(f"{__package__}.core.{module}"), function)
        ).parameters
        takes_any = any(p.kind is p.VAR_KEYWORD for p in accepts.values())
        args = []
        for key, value in node["inputs"].items():
            if key not in accepts and not takes_any:
                continue  # a widget only the studio shows (an upload button)
            if (node_id, key) in bound:
                args.append(f"{key}=values[{bound[(node_id, key)]!r}]")
            elif isinstance(value, list) and len(value) == 2 and str(value[0]) in prompt:
                args.append(f"{key}={var(value[0])}[{value[1]}]")
            elif key in getattr(cls, "FILES", ()) and isinstance(value, str) and value:
                source = (input_dir / value).resolve()
                if source.is_file():  # a reference file the step reads: shipped with the package
                    bundled[source.name] = source
                    args.append(f'{key}=str(HERE / "reference" / {source.name!r})')
                else:
                    args.append(f"{key}={value!r}")
            else:
                args.append(f"{key}={value!r}")
        lines.append(
            f"    {var(node_id)} = {module}.{function}(rt.ctx({function!r}), {', '.join(args)})"
            f"  # {title_of(release, node_id)}"
        )
    if bundled:
        (package / "reference").mkdir()
        for file_name, source in bundled.items():
            shutil.copyfile(source, package / "reference" / file_name)
    examples = []
    for example in contract.get("examples") or []:
        home = package / "examples" / example["id"]
        for rel, text in (example.get("files") or {}).items():
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text(text, encoding="utf-8")
        named = {
            "/".join(rel.split("/")[: i + 1])
            for rel in example.get("files") or {}
            for i in range(rel.count("/") + 1)
        }
        given = {
            k: (
                f"@examples/{example['id']}/{v.strip('/')}"
                if isinstance(v, str) and v.strip("/") in named
                else v
            )
            for k, v in example["given"].items()
        }
        examples.append(
            {
                "id": example["id"],
                "given": given,
                "expect": example["expect"],
                "note": example.get("note", ""),
            }
        )
    about = descriptions(release)
    inputs = {
        item["name"]: [
            item.get("default"),
            (item.get("type") or "STRING"),
            about.get(item["name"], ""),
        ]
        for item in release["signature"]["inputs"]
    }
    literal = lambda value: pprint.pformat(value, width=100, sort_dicts=False)  # noqa: E731
    (package / "pipeline.py").write_text(
        PIPELINE.format(
            title=literal(contract.get("title") or name),
            semver=literal(release["semver"]),
            inputs=literal(inputs),
            outputs=literal([o["name"] for o in release["signature"]["outputs"]]),
            folders=literal(sorted(folders)),
            examples=literal(examples),
            purpose=literal(contract.get("purpose") or release.get("description") or ""),
            doc=(contract.get("title") or name).replace('"', "'"),
            body="\n".join(lines) or "    pass",
        ),
        encoding="utf-8",
    )
    (package / "cli.py").write_text(CLI.replace("{prog}", name), encoding="utf-8")
    (package / "__main__.py").write_text("from .cli import main\n\nmain()\n", encoding="utf-8")
    (target / "tests").mkdir()
    (target / "tests" / "test_examples.py").write_text(
        TESTS.replace("{pkg}", pkg), encoding="utf-8"
    )
    deps = sorted(
        {
            DEPENDENCIES[m]
            for m in DEPENDENCIES
            if any(
                re.search(rf"^\s*(import|from) {m}\b", p.read_text(encoding="utf-8"), re.M)
                for p in (package / "core").glob("*.py")
            )
        }
    )
    pins = [f'"{d}=={metadata.version(d)}"' for d in deps]
    (target / "pyproject.toml").write_text(
        PYPROJECT.format(
            name=name,
            version=release["semver"],
            pkg=pkg,
            deps=", ".join(pins),
            description=json.dumps(
                (contract.get("purpose") or release.get("description") or name)[:200]
            ),
        ),  # fmt: skip
        encoding="utf-8",
    )
    (target / "README.md").write_text(readme(release, contract, pkg, inputs), encoding="utf-8")
    (target / ".gitignore").write_text(
        "out/\n*.egg-info/\n__pycache__/\ndist/\nbuild/\n.venv/\n", encoding="utf-8"
    )
    (target / ".github" / "workflows").mkdir(parents=True)
    (target / ".github" / "workflows" / "test.yml").write_text(
        CI.replace("{prog}", name), encoding="utf-8"
    )
    (target / "EXPORT.json").write_text(
        json.dumps(
            {
                "workflow": contract.get("title") or name,
                "api": name,
                "release": release["semver"],
                "workflow_id": release.get("workflow_id"),
                "studio_version": release.get("version"),
                "core": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                    for p in sorted((package / "core").glob("*.py"))
                },
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return target


def readme(release: dict, contract: dict, pkg: str, inputs: dict) -> str:
    name = release["name"]
    rows = "\n".join(
        f"| `--{k.replace('_', '-')}` | {d or ''} | `{str(v)[:40]!s}` |".replace("\n", " ")
        for k, (v, _, d) in inputs.items()
    )
    steps = "\n".join(
        f"{i + 1}. **{s['id']}**: {s['purpose']}" for i, s in enumerate(contract.get("steps") or [])
    )
    return README.format(
        title=contract.get("title") or name, purpose=contract.get("purpose") or release.get("description") or "",
        name=name, pkg=pkg, semver=release["semver"], rows=rows, steps=steps or "(no contract)",
        outputs=", ".join(f"`{o['name']}`" for o in release["signature"]["outputs"]),
    )  # fmt: skip


# --- 3 test and 4 package -------------------------------------------------------------------------
def run(command: list, cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def install(repo: Path, venv: Path) -> tuple[bool, str]:
    """A fresh environment with the repo installed: uv, offline from its cache first."""
    uv = shutil.which("uv")
    if uv:
        made = run(
            [
                uv,
                "venv",
                "--python",
                f"{sys.version_info.major}.{sys.version_info.minor}",
                str(venv),
            ]
        )
        if made.returncode:
            return False, made.stderr[-800:]
        python = venv / "bin" / "python"
        for extra in (["--offline"], []):
            done = run([uv, "pip", "install", "--python", str(python), *extra, str(repo)])
            if done.returncode == 0:
                return True, "installed with uv" + (" (offline)" if extra else "")
        return False, done.stderr[-800:]
    made = run([sys.executable, "-m", "venv", str(venv)])
    done = (
        run([str(venv / "bin" / "python"), "-m", "pip", "install", str(repo)])
        if not made.returncode
        else made
    )
    return done.returncode == 0, (done.stdout + done.stderr)[-800:]


def test_and_package(repo: Path, dist: Path, name: str) -> dict:
    """Install in a fresh environment, run `<name> check`, then build the wheel, sdist and zip."""
    venv = repo.parent / "venv"
    shutil.rmtree(venv, ignore_errors=True)
    ok, how = install(repo, venv)
    result = {"installed": ok, "install": how}
    if ok:
        checked = run([str(venv / "bin" / name), "check"], cwd=repo.parent)
        result |= {
            "check_passed": checked.returncode == 0,
            "check": (checked.stdout + checked.stderr)[-3000:],
        }
    else:
        result |= {"check_passed": False, "check": "not run: the install failed"}
    dist.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    built = None
    for extra in (["--offline"], []) if uv else ():
        built = run([uv, "build", "--out-dir", str(dist), *extra, str(repo)])
        if built.returncode == 0:
            break
    result["built"] = bool(built and built.returncode == 0)
    if built and built.returncode:
        result["build"] = built.stderr[-800:]
    archive = dist / f"{repo.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(repo.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and ".egg-info" not in str(path):
                z.write(path, Path(repo.name) / path.relative_to(repo))
    result["files"] = sorted(p.name for p in dist.iterdir() if not p.name.startswith("."))
    shutil.rmtree(venv, ignore_errors=True)
    return result


def export(release: dict, contract: dict | None, output_dir: Path, input_dir: Path) -> dict:
    """The whole export: readiness, the repo, its test in a fresh environment, the packages."""
    ready = readiness(release, contract)
    if not ready["ready"]:
        return ready
    home = output_dir / "exports" / f"{release['name']}-{release['semver']}"
    repo = generate(release, contract, home / release["name"], input_dir)
    result = test_and_package(repo, home / "dist", release["name"])
    return {
        **ready,
        **result,
        "folder": str(home.relative_to(output_dir)),
        "repo": str(repo.relative_to(output_dir)),
    }


# --- the generated files --------------------------------------------------------------------------
PIPELINE = '''"""{doc}: the workflow as a program. Generated by Agent Workflow Studio from the published
release; export again rather than edit (the steps it calls are in core/, unchanged from the studio)."""

from pathlib import Path

from .core import runner, steps  # noqa: F401

HERE = Path(__file__).parent
TITLE, VERSION = {title}, {semver}
PURPOSE = {purpose}
# The workflow's App inputs: name -> [default, type, description].
INPUTS = {inputs}
OUTPUTS = {outputs}
FOLDERS = {folders}  # inputs that name a data folder
EXAMPLES = {examples}


def run(out, labels=None, **given):
    """Run the workflow; `given` overrides any input. Returns the runtime (checks, outputs, lines)."""
    values = {{k: (given[k] if given.get(k) is not None else d) for k, (d, _, _) in INPUTS.items()}}
    input_dir = Path(out)
    for name in FOLDERS:
        folder = Path(str(values[name]).replace("@examples", str(HERE / "examples"), 1)).resolve()
        if not folder.is_dir():
            raise SystemExit(f"No folder {{folder}} for --{{name.replace('_', '-')}}.")
        input_dir, values[name] = folder.parent, folder.name
    rt = runner.Runtime(Path(out), input_dir, labels)
{body}
    return rt
'''

CLI = '''"""The command line: run on your data, review in a CSV, check the examples, write a config file."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

import yaml

from . import pipeline
from .core import runner
from .core.checks import compare_example

PROG = "{prog}"
TRUE = {"1", "true", "yes", "on"}


def cast(value, kind):
    if value is None:
        return None
    if kind == "INT":
        return int(value)
    if kind == "FLOAT":
        return float(value)
    if kind == "BOOLEAN":
        return value if isinstance(value, bool) else str(value).lower() in TRUE
    return value


def given(args) -> dict:
    """Input values: the config file, then the options (an option wins)."""
    values = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {} if args.config else {}
    for name, (_, kind, _) in pipeline.INPUTS.items():
        if getattr(args, name, None) is not None:
            values[name] = getattr(args, name)
        if name in values:
            values[name] = cast(values[name], kind)
    return values


def run(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    store = out / "reviews.csv"  # every decision so far
    labels = runner.read_reviews(store) | runner.read_reviews(Path(args.reviews) if args.reviews else None)
    runner.write_reviews(store, labels)
    rt = pipeline.run(out, labels, **given(args))
    for line in rt.lines:
        print(line)
    written = runner.write_outputs(rt, out)
    for name, path in written.items():
        print(f"→ {name}: {path}")
    asked = runner.review_batch(rt, out / "review_batch.csv")
    if asked:
        print(f"→ {asked} pairs to review: fill the decision column of {out / 'review_batch.csv'} (same / different),"
              f" then run again with --reviews {out / 'review_batch.csv'}")
    failed = [c for c in rt.checks if not c["passed"]]
    print(f"{len(rt.checks) - len(failed)} of {len(rt.checks)} checks passed")
    return 1 if failed else 0


def check_examples() -> list[str]:
    """Every example's expectations and every stage Check, on the shipped example data."""
    problems = []
    for example in pipeline.EXAMPLES:
        with tempfile.TemporaryDirectory() as tmp:
            rt = pipeline.run(Path(tmp), {}, **example["given"])
            data = {name: runner.plain(value) for name, value in rt.outputs.items()}
            problems += [f"{example['id']}: {m}" for m in compare_example(example["expect"], data, {"lines": rt.lines})]
            problems += [f"{example['id']}: check failed · {c['says']} · {c['evidence']}" for c in rt.checks if not c["passed"]]
    return problems


def check(_args) -> int:
    if not pipeline.EXAMPLES:
        print("No examples to run.")
        return 0
    problems = check_examples()
    for p in problems:
        print("✗", p)
    print(f"{len(pipeline.EXAMPLES)} example(s): " + ("all passed" if not problems else f"{len(problems)} problem(s)"))
    return 1 if problems else 0


def init(args) -> int:
    """A config file with every input, its description and its default, to edit."""
    path = Path(args.path)
    if path.exists():
        print(f"{path} exists; not overwritten.")
        return 1
    lines = [f"# {pipeline.TITLE} {pipeline.VERSION}: input values. Edit, then: {PROG} run --config {path}"]
    for name, (default, _, description) in pipeline.INPUTS.items():
        lines.append(f"\\n# {description}" if description else "")
        if isinstance(default, str) and "\\n" in default:
            lines.append(f"{name}: |\\n" + "\\n".join("  " + x for x in default.splitlines()))
        else:
            lines.append(f"{name}: {json.dumps(default, ensure_ascii=False)}")
    path.write_text("\\n".join(lines).strip() + "\\n", encoding="utf-8")
    print(f"Wrote {path}")
    return 0


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog=PROG, description=pipeline.PURPOSE.replace("%", "%%"))
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="run on your data")
    for name, (default, kind, description) in pipeline.INPUTS.items():
        shown = str(default).splitlines()[0][:40] if default not in (None, "") else ""
        tip = f"{description} (default: {shown})".replace("%", "%%")
        go.add_argument(f"--{name.replace('_', '-')}", dest=name, default=None, help=tip)
    go.add_argument("--config", help=f"a YAML file of input values (write one with: {PROG} init)")
    go.add_argument("--reviews", help="your filled-in review CSV (decision: same / different)")
    go.add_argument("--out", default="out", help="where results, reviews and run folders go")
    sub.add_parser("check", help="run the examples and every stage Check; exit 1 if any fails")
    start = sub.add_parser("init", help="write a config file with every input to edit")
    start.add_argument("path", nargs="?", default="config.yaml")
    args = parser.parse_args(argv)
    sys.exit({"run": run, "check": check, "init": init}[args.command](args))
'''

TESTS = '''"""The workflow's own tests: its contract's examples and stage Checks (the studio ran the same)."""

from {pkg}.cli import check_examples


def test_examples_and_checks_pass():
    assert check_examples() == []
'''

PYPROJECT = """[project]
name = "{name}"
version = "{version}"
description = {description}
requires-python = ">=3.11"
dependencies = [{deps}]

[project.scripts]
{name} = "{pkg}.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["{pkg}*"]

[tool.setuptools.package-data]
"*" = ["reference/*", "examples/**/*"]
"""

README = """# {title}

{purpose}

Exported from Agent Workflow Studio (release {semver} of the workflow API `{name}`). It is the same
logic the studio runs (`{pkg}/core/`), without ComfyUI. Regenerate it from the studio rather than
editing `{pkg}/pipeline.py`.

## Install and run

```bash
pip install .                     # or: pip install dist/*.whl
{name} init                       # writes config.yaml: every input, described, to edit
{name} run --config config.yaml --out out
{name} check                      # the examples and every stage Check
```

Results go to `out/`: {outputs}. When the workflow asks you to review pairs, it writes
`out/review_batch.csv`: fill its `decision` column (same / different), then run again with
`--reviews out/review_batch.csv`. Every decision so far is kept in `out/reviews.csv`.

## Inputs

| Option | What it is | Default |
|---|---|---|
{rows}

## Steps

{steps}
"""

CI = """name: test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install . pytest
      - run: {prog} check
      - run: pytest -q
"""


def latest_release(workflow_id: str) -> dict | None:
    """The newest published release of this workflow (under any API name)."""
    from .project_repo import projects_root
    from .workflow_api import published_dir

    found = []
    for path in published_dir(projects_root()).glob("*.json"):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if spec.get("workflow_id") == workflow_id:
            found.append(spec)
    return max(found, key=lambda s: s.get("published_at", ""), default=None)


def export_workflow(workflow_id: str, check_only: bool = False) -> dict:
    """The button's and the agent's export: readiness only, or the whole export with download links."""
    import folder_paths

    from .native_layers import layers
    from .project_repo import ProjectRepo, projects_root
    from .workflow_api import prompt_hash

    release = latest_release(workflow_id)
    repo = ProjectRepo(projects_root(), workflow_id)
    contract = (layers(release["workflow"]) if release else None) or repo.load_contract()
    runs = repo.records("pipeline")
    latest = runs[-1].get("prompt_hash", "") if runs else ""
    ready = readiness(release, contract)
    if release and latest and latest != prompt_hash(release["prompt"]):
        ready["todo"].append(
            f"The workflow changed since release {release['semver']} was published: publish again to export the changes."
        )
    if check_only or not ready["ready"]:
        return ready
    output = Path(folder_paths.get_output_directory())
    result = export(release, contract, output, Path(folder_paths.get_input_directory()))
    if "folder" in result:
        dist = f"{result['folder']}/dist"
        result["downloads"] = [
            {"file": f, "url": f"/view?filename={f}&subfolder={dist}&type=output"}
            for f in result.get("files", [])
        ]
    return {**result, "todo": ready["todo"]}
