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
import os
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
DOCS = HERE / "export_docs"  # what a node's DOCS name: markdown the exported README ends with
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
                "Not published yet. Publishing needs only green checks (comfy_run_examples: design, stage "
                "Checks, examples, no regression); an unproven request does not block it. Only the user "
                "publishes: Contract tab → API → Publish as API, which says why if it refuses."
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


def program(
    release: dict, contract: dict, package: Path, input_dir: Path, module: str, home: str
) -> dict:
    """One release as a module of the package (pipeline.py, or pipeline_<command>.py for a companion):
    its nodes as calls to their core steps, its inputs, its examples (sample files under
    examples/<home>/). Returns what the package needs from it."""
    nodes, prompt = pack_nodes(), release["prompt"]
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
        raise ValueError(
            f"{release['name']}: the export handles one data folder input; it has several."
        )
    lines, packages, extras, used, docs = [], set(), {}, {"runner"}, []
    for node_id in order(prompt):
        node = prompt[node_id]
        cls = nodes.get(node["class_type"])
        if node["class_type"] in UI_ONLY or not getattr(cls, "EXPORT", None):
            continue
        packages |= set(getattr(cls, "PACKAGES", ()))
        extras |= getattr(cls, "EXTRAS", {})
        docs += [d for d in getattr(cls, "DOCS", ()) if d not in docs]
        step_module, function = cls.EXPORT.split(":")
        used.add(step_module)
        accepts = inspect.signature(
            getattr(importlib.import_module(f"{__package__}.core.{step_module}"), function)
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
                    (package / "reference").mkdir(exist_ok=True)
                    shutil.copyfile(source, package / "reference" / source.name)
                    args.append(f'{key}=str(HERE / "reference" / {source.name!r})')
                else:
                    args.append(f"{key}={value!r}")
            else:
                args.append(f"{key}={value!r}")
        lines.append(
            f"    {var(node_id)} = {step_module}.{function}(rt.ctx({function!r}), {', '.join(args)})"
            f"  # {title_of(release, node_id)}"
        )
    examples = []
    for example in contract.get("examples") or []:
        where = "/".join(x for x in (home, example["id"]) if x)
        for rel, text in (example.get("files") or {}).items():
            path = package / "examples" / where / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        named = {
            "/".join(rel.split("/")[: i + 1])
            for rel in example.get("files") or {}
            for i in range(rel.count("/") + 1)
        }
        given = {
            k: (
                f"@examples/{where}/{v.strip('/')}"
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
    inputs = {  # a data folder is any path on the user's machine, not the studio's folder list
        item["name"]: [
            item.get("default"),
            item.get("type") or "STRING",
            about.get(item["name"], ""),
            None if item["name"] in folders else item.get("choices"),
        ]
        for item in release["signature"]["inputs"]
    }
    literal = lambda value: pprint.pformat(value, width=100, sort_dicts=False)  # noqa: E731
    (package / f"{module}.py").write_text(
        PIPELINE.format(
            title=literal(contract.get("title") or release["name"]),
            semver=literal(release["semver"]),
            inputs=literal(inputs),
            outputs=literal([o["name"] for o in release["signature"]["outputs"]]),
            folders=literal(sorted(folders)),
            examples=literal(examples),
            purpose=literal(contract.get("purpose") or release.get("description") or ""),
            doc=(contract.get("title") or release["name"]).replace('"', "'"),
            body="\n".join(lines) or "    pass",
            modules=", ".join(sorted(used)),
        ),
        encoding="utf-8",
    )
    return {
        "inputs": inputs,
        "examples": examples,
        "packages": packages,
        "extras": extras,
        "docs": docs,
        "release": release,
        "contract": contract,
    }


def command_of(main: str, other: str) -> str:
    """A companion's command: contacts-nayose-generate -> generate."""
    return other[len(main) + 1 :] if other.startswith(f"{main}-") else other


def generate(
    release: dict, contract: dict | None, target: Path, input_dir: Path, also: list | tuple = ()
) -> Path:
    """Write the repo into `target` (replaced): the release as the program, each release in `also`
    (with its contract) as one more command. Returns its path."""
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
    main = program(release, contract, package, input_dir, "pipeline", "")
    commands = {}
    for other, other_contract in also:
        command = command_of(name, other["name"])
        commands[command] = program(
            other, other_contract or {}, package, input_dir, f"pipeline_{slug(command)}", command
        )
    packages, extras, docs = set(main["packages"]), dict(main["extras"]), list(main["docs"])
    for part in commands.values():
        packages |= part["packages"]
        extras |= part["extras"]
        docs += [d for d in part["docs"] if d not in docs]
    for part_name in sorted(packages):  # pack code a step needs, copied as it is
        shutil.copytree(
            HERE / part_name, package / part_name, ignore=shutil.ignore_patterns("__pycache__")
        )
    (package / "commands.py").write_text(
        '"""The program\'s other commands: one published workflow each (generated)."""\n\n'
        + "".join(f"from . import pipeline_{slug(c)}\n" for c in commands)
        + "\nCOMMANDS = {"
        + ", ".join(f"{c!r}: pipeline_{slug(c)}" for c in commands)
        + "}\n",
        encoding="utf-8",
    )
    (package / "cli.py").write_text(CLI.replace("{prog}", name), encoding="utf-8")
    (package / "__main__.py").write_text("from .cli import main\n\nmain()\n", encoding="utf-8")
    (target / "tests").mkdir()
    (target / "tests" / "test_examples.py").write_text(
        TESTS.replace("{pkg}", pkg), encoding="utf-8"
    )
    sources = [p for part in ("core", *sorted(packages)) for p in (package / part).glob("*.py")]
    deps = sorted(
        {
            DEPENDENCIES[m]
            for m in DEPENDENCIES
            if any(
                re.search(rf"^\s*(import|from) {m}\b", p.read_text(encoding="utf-8"), re.M)
                for p in sources
            )
        }
    )
    pins = [f'"{d}=={metadata.version(d)}"' for d in deps]
    optional = "".join(
        f"{extra} = [{', '.join(json.dumps(r) for r in spec['requires'])}]\n"
        for extra, spec in extras.items()
    )
    (target / "pyproject.toml").write_text(
        PYPROJECT.format(
            name=name,
            version=release["semver"],
            pkg=pkg,
            deps=", ".join(pins),
            description=json.dumps(
                (contract.get("purpose") or release.get("description") or name)[:200]
            ),
            optional=f"\n[project.optional-dependencies]\n{optional}" if optional else "",
        ),  # fmt: skip
        encoding="utf-8",
    )
    (target / "README.md").write_text(
        readme(main, commands, extras, pkg, package, docs), encoding="utf-8"
    )
    (target / ".gitignore").write_text(
        "out/\n*.egg-info/\n__pycache__/\ndist/\nbuild/\n.venv/\n", encoding="utf-8"
    )
    (target / ".github" / "workflows").mkdir(parents=True)
    # CI also runs the program the way a user does, on the first example's data (init, then run).
    folders_given = [
        f"--{k.replace('_', '-')} {v.replace('@examples', f'{pkg}/examples')}"
        for k, v in (main["examples"][0]["given"].items() if main["examples"] else [])
        if isinstance(v, str) and v.startswith("@examples")
    ]
    as_user = (
        f"      - run: {name} init config.yaml\n"
        f"      - run: {name} run --config config.yaml {' '.join(folders_given)} --out out\n"
        if folders_given
        else ""
    )
    (target / ".github" / "workflows" / "test.yml").write_text(
        CI.replace("{prog}", name) + as_user, encoding="utf-8"
    )
    (target / "EXPORT.json").write_text(
        json.dumps(
            {
                "workflow": contract.get("title") or name,
                "api": name,
                "release": release["semver"],
                "workflow_id": release.get("workflow_id"),
                "studio_version": release.get("version"),
                "commands": {
                    c: {"api": part["release"]["name"], "release": part["release"]["semver"]}
                    for c, part in commands.items()
                },
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


def options_table(inputs: dict) -> str:
    def shown(v) -> str:  # a multi-line default (the column map) lives in the config file
        return "in config.yaml (`init` writes it)" if isinstance(v, str) and "\n" in v else f"`{v}`"

    rows = "\n".join(
        f"| `--{k.replace('_', '-')}` | {d or ''} | {shown(v)} |"
        for k, (v, _, d, *_) in inputs.items()
    )
    return "| Option | What it is | Default |\n|---|---|---|\n" + rows


def sentence(text: str) -> str:
    return text[:1].upper() + text[1:] if text else ""


def anchor(heading: str) -> str:
    """GitHub's link to a heading: lower case, punctuation dropped, spaces as hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def readme(
    main: dict, commands: dict, extras: dict, pkg: str, package: Path, docs: list = ()
) -> str:
    release, contract = main["release"], main["contract"]
    name = release["name"]
    texts = [
        (DOCS / d).read_text(encoding="utf-8").replace("{name}", name).replace("{pkg}", pkg)
        for d in docs
    ]
    headings = [h for t in texts for h in re.findall(r"^# (.+)$", t, re.M)]
    steps = "\n".join(
        f"{i + 1}. **{s['id']}**: {s['purpose']}" for i, s in enumerate(contract.get("steps") or [])
    )
    decided = "\n".join(
        f"- **{d['decision']}** {sentence(d.get('why', ''))}."
        + (f" Instead of: {d['instead_of']}." if d.get("instead_of") else "")
        for d in contract.get("decisions") or []
    )
    more = "".join(
        f"\n### `{name} {c}`\n\n{part['contract'].get('purpose') or part['release'].get('description') or ''}\n\n"
        f"{options_table(part['inputs'])}\n| `--out` | Where its results and run folders go | `{c}` |\n"
        for c, part in commands.items()
    )
    files = sorted(p.name for p in (package / "reference").glob("*"))
    return README.format(
        title=contract.get("title") or name, purpose=contract.get("purpose") or release.get("description") or "",
        guide="\n\nHow it works and why: " + " · ".join(f"[{h}](#{anchor(h)})" for h in headings) + "."
        if headings else "",
        docs="".join(f"\n---\n\n{t.strip()}\n" for t in texts),
        name=name, pkg=pkg, semver=release["semver"], rows=options_table(main["inputs"]), steps=steps or "(no contract)",
        outputs=", ".join(f"`{o['name']}`" for o in release["signature"]["outputs"]),
        commands=f"\n## More commands\n{more}" if more else "",
        extras="".join(f"\n## Optional: {e}\n\n`pip install \".[{e}]\"`, then {spec['how'].replace('{pkg}', pkg)}\n" for e, spec in extras.items()),
        decisions=f"\n## Decisions\n\n{decided}\n" if decided else "",
        reference=(
            f"\n## Reference data\n\nShipped in `{pkg}/reference/`, as the studio used it: "
            + ", ".join(f"`{f}`" for f in files) + ".\n"
            if files else ""
        ),
    )  # fmt: skip


# --- 3 test and 4 package -------------------------------------------------------------------------
def run(command: list, cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def venv_bin(venv: Path, program: str) -> Path:
    """A program in a virtual environment: Scripts\\<name>.exe on Windows, bin/<name> elsewhere."""
    return venv / "Scripts" / f"{program}.exe" if os.name == "nt" else venv / "bin" / program


def install(target: Path, venv: Path) -> tuple[bool, str]:
    """A fresh environment with `target` (the built wheel, or the repo) installed: uv, offline from
    its cache first."""
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
        python = venv_bin(venv, "python")
        for extra in (["--offline"], []):
            done = run([uv, "pip", "install", "--python", str(python), *extra, str(target)])
            if done.returncode == 0:
                return True, "installed with uv" + (" (offline)" if extra else "")
        return False, done.stderr[-800:]
    made = run([sys.executable, "-m", "venv", str(venv)])
    done = (
        run([str(venv_bin(venv, "python")), "-m", "pip", "install", str(target)])
        if not made.returncode
        else made
    )
    return done.returncode == 0, (done.stdout + done.stderr)[-800:]


def test_and_package(repo: Path, dist: Path, name: str) -> dict:
    """Build the wheel and sdist, install the wheel in a fresh environment, run `<name> check`,
    then zip the repo (the source tree is never built in place)."""
    dist.mkdir(parents=True, exist_ok=True)
    uv, built = shutil.which("uv"), None
    for extra in (["--offline"], []) if uv else ():
        built = run([uv, "build", "--out-dir", str(dist), *extra, str(repo)])
        if built.returncode == 0:
            break
    result = {"built": bool(built and built.returncode == 0)}
    if built and built.returncode:
        result["build"] = built.stderr[-800:]
    wheel = next(iter(sorted(dist.glob("*.whl"))), None)
    venv = repo.parent / "venv"
    shutil.rmtree(venv, ignore_errors=True)
    ok, how = install(wheel or repo, venv)
    result |= {"installed": ok, "install": how}
    if ok:
        checked = run([str(venv_bin(venv, name)), "check"], cwd=repo.parent)
        result |= {
            "check_passed": checked.returncode == 0,
            "check": (checked.stdout + checked.stderr)[-3000:],
        }
    else:
        result |= {"check_passed": False, "check": "not run: the install failed"}
    archive = dist / f"{repo.name}.zip"
    skip = {"__pycache__", "build", ".venv"}
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(repo.rglob("*")):
            if path.is_file() and not skip & set(path.parts) and ".egg-info" not in str(path):
                z.write(path, Path(repo.name) / path.relative_to(repo))
    result["files"] = sorted(p.name for p in dist.iterdir() if not p.name.startswith("."))
    shutil.rmtree(venv, ignore_errors=True)
    return result


def export(
    release: dict, contract: dict | None, output_dir: Path, input_dir: Path, also: list | tuple = ()
) -> dict:
    """The whole export: readiness, the repo, its test in a fresh environment, the packages."""
    ready = readiness(release, contract)
    for other, other_contract in also:  # each companion command must be exportable too
        more = readiness(other, other_contract)
        ready["blockers"] += [f"{other['name']}: {b}" for b in more["blockers"]]
        ready["ready"] = ready["ready"] and more["ready"]
    if not ready["ready"]:
        return ready
    home = output_dir / "exports" / f"{release['name']}-{release['semver']}"
    repo = generate(release, contract, home / release["name"], input_dir, also)
    result = test_and_package(repo, home / "dist", release["name"])
    return {
        **ready,
        **result,
        "folder": str(home.relative_to(output_dir)),
        "repo": str(repo.relative_to(output_dir)),
        "where": f"in ComfyUI's output folder, under {repo.relative_to(output_dir)} (not the workflow's files)",
    }


# --- the generated files --------------------------------------------------------------------------
PIPELINE = '''"""{doc}: the workflow as a program. Generated by Agent Workflow Studio from the published
release; export again rather than edit (the steps it calls are in core/, unchanged from the studio)."""

from pathlib import Path

from .core import {modules}  # noqa: F401

HERE = Path(__file__).parent
TITLE, VERSION = {title}, {semver}
PURPOSE = {purpose}
# The workflow's App inputs: name -> [default, type, description, choices].
INPUTS = {inputs}
OUTPUTS = {outputs}
FOLDERS = {folders}  # inputs that name a data folder
EXAMPLES = {examples}


def run(out, labels=None, **given):
    """Run the workflow; `given` overrides any input. Returns the runtime (checks, outputs, lines)."""
    values = {{k: (given[k] if given.get(k) is not None else d) for k, (d, *_) in INPUTS.items()}}
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

CLI = """\"\"\"The command line: run on your data, review in a CSV, check the examples, write a config file;
and one command per companion workflow (commands.py).\"\"\"

import argparse
import json
import sys
import tempfile
from pathlib import Path

import yaml

from . import pipeline
from .commands import COMMANDS
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


def given(args, module) -> dict:
    \"\"\"Input values: the config file, then the options (an option wins).\"\"\"
    config = getattr(args, "config", None)
    values = (yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}) if config else {}
    for name, (_, kind, *_) in module.INPUTS.items():
        if getattr(args, name, None) is not None:
            values[name] = getattr(args, name)
        if name in values:
            values[name] = cast(values[name], kind)
    return values


def run(args, module=pipeline) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    store = out / "reviews.csv"  # every decision so far
    labels = runner.read_reviews(store) | runner.read_reviews(Path(args.reviews) if getattr(args, "reviews", None) else None)
    runner.write_reviews(store, labels)
    rt = module.run(out, labels, **given(args, module))
    for line in rt.lines:
        print(line)
    for name, path in runner.write_outputs(rt, out).items():
        print(f"→ {name}: {path}")
    asked = runner.review_batch(rt, out / "review_batch.csv")
    if asked:
        print(f"→ {asked} pairs to review: fill the decision column of {out / 'review_batch.csv'} (same / different),"
              f" then run again with --reviews {out / 'review_batch.csv'}")
    failed = [c for c in rt.checks if not c["passed"]]
    print(f"{len(rt.checks) - len(failed)} of {len(rt.checks)} checks passed")
    return 1 if failed else 0


def check_examples() -> list[str]:
    \"\"\"Every example's expectations and every stage Check, on the shipped example data.\"\"\"
    problems = []
    for command, module in [("run", pipeline), *COMMANDS.items()]:
        for example in module.EXAMPLES:
            with tempfile.TemporaryDirectory() as tmp:
                rt = module.run(Path(tmp), {}, **example["given"])
                data = {name: runner.plain(value) for name, value in rt.outputs.items()}
                where = f"{command} {example['id']}"
                problems += [f"{where}: {m}" for m in compare_example(example["expect"], data, {"lines": rt.lines})]
                problems += [f"{where}: check failed · {c['says']} · {c['evidence']}" for c in rt.checks if not c["passed"]]
    return problems


def check(_args) -> int:
    total = len(pipeline.EXAMPLES) + sum(len(m.EXAMPLES) for m in COMMANDS.values())
    if not total:
        print("No examples to run.")
        return 0
    problems = check_examples()
    for p in problems:
        print("✗", p)
    print(f"{total} example(s): " + ("all passed" if not problems else f"{len(problems)} problem(s)"))
    return 1 if problems else 0


def init(args) -> int:
    \"\"\"A config file with every input, its description and its default, to edit.\"\"\"
    path = Path(args.path)
    if path.exists():
        print(f"{path} exists; not overwritten.")
        return 1
    lines = [f"# {pipeline.TITLE} {pipeline.VERSION}: input values. Edit, then: {PROG} run --config {path}"]
    for name, (default, _, description, *_) in pipeline.INPUTS.items():
        lines.append(f"\\n# {description}" if description else "")
        if isinstance(default, str) and "\\n" in default:
            lines.append(f"{name}: |\\n" + "\\n".join("  " + x for x in default.splitlines()))
        else:
            lines.append(f"{name}: {json.dumps(default, ensure_ascii=False)}")
    path.write_text("\\n".join(lines).strip() + "\\n", encoding="utf-8")
    print(f"Wrote {path}")
    return 0


def options(parser, module) -> None:
    \"\"\"One option per input of a workflow, with its description, default and choices.\"\"\"
    for name, (default, kind, description, *rest) in module.INPUTS.items():
        shown = str(default).splitlines()[0][:40] if default not in (None, "") else ""
        tip = f"{description} (default: {shown})".replace("%", "%%")
        choices = rest[0] if rest and rest[0] else None
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name, default=None, help=tip, choices=choices)


def main(argv=None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows writes a pipe or file in its code page otherwise
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog=PROG, description=pipeline.PURPOSE.replace("%", "%%"))
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="run on your data")
    options(go, pipeline)
    go.add_argument("--config", help=f"a YAML file of input values (write one with: {PROG} init)")
    go.add_argument("--reviews", help="your filled-in review CSV (decision: same / different)")
    go.add_argument("--out", default="out", help="where results, reviews and run folders go")
    sub.add_parser("check", help="run the examples and every stage Check; exit 1 if any fails")
    start = sub.add_parser("init", help="write a config file with every input to edit")
    start.add_argument("path", nargs="?", default="config.yaml")
    for command, module in COMMANDS.items():
        more = sub.add_parser(command, help=module.PURPOSE.replace("%", "%%"))
        options(more, module)
        more.add_argument("--out", default=command, help="where its results and run folders go")
    args = parser.parse_args(argv)
    if args.command in COMMANDS:
        sys.exit(run(args, COMMANDS[args.command]))
    sys.exit({"run": run, "check": check, "init": init}[args.command](args))
"""

TESTS = '''"""The workflow's own tests: its contract's examples and stage Checks (the studio ran the same)."""

from {pkg}.cli import check_examples


def test_examples_and_checks_pass():
    assert check_examples() == []
'''

PYPROJECT = """[project]
name = "{name}"
version = "{version}"
description = {description}
requires-python = ">=3.12"
dependencies = [{deps}]

{optional}
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

{purpose}{guide}

Exported from Agent Workflow Studio (release {semver} of the workflow API `{name}`). It is the same
logic the studio runs (`{pkg}/core/`), without ComfyUI. Regenerate it from the studio rather than
editing `{pkg}/pipeline.py`.

## Install and run

```bash
pip install .                          # or the wheel the studio built
{name} init                    # config.yaml: every input, described, to edit
{name} run --config config.yaml --out out
{name} check                   # the examples and every stage Check
```

The same commands work on Windows, macOS and Linux (Python 3.12 or later; on Windows, in
PowerShell or cmd, with `py -m pip install .`).

Results go to `out/`: {outputs}. When the workflow asks you to review pairs, it writes
`out/review_batch.csv`: fill its `decision` column (same / different), then run again with
`--reviews out/review_batch.csv`. Every decision so far is kept in `out/reviews.csv`.

## Inputs

{rows}
{commands}

## Steps

{steps}
{decisions}{extras}{reference}{docs}"""

CI = """name: test
on: [push, pull_request]
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    runs-on: ${{ matrix.os }}
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


def companions(name: str) -> list[dict]:
    """Published releases named <name>-<command>: each becomes `<name> <command>` in the program."""
    from .project_repo import projects_root
    from .workflow_api import published_dir

    found = []
    for path in sorted(published_dir(projects_root()).glob(f"{name}-*.json")):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return found


def export_workflow(workflow_id: str, check_only: bool = False) -> dict:
    """The button's and the agent's export: readiness only, or the whole export with download links.
    Releases published as <name>-<command> (a data generator, a benchmark) come along as commands."""
    import folder_paths

    from .native_layers import layers
    from .project_repo import ProjectRepo, projects_root
    from .workflow_api import prompt_hash

    release = latest_release(workflow_id)
    repo = ProjectRepo(projects_root(), workflow_id)
    contract = (layers(release["workflow"]) if release else None) or repo.load_contract()
    runs = repo.records("pipeline")
    latest = runs[-1].get("prompt_hash", "") if runs else ""
    also = (
        [(other, layers(other["workflow"])) for other in companions(release["name"])]
        if release
        else []
    )
    ready = readiness(release, contract)
    if also:
        ready["commands"] = {command_of(release["name"], o["name"]): o["semver"] for o, _ in also}
    if release and latest and latest != prompt_hash(release["prompt"]):
        ready["todo"].append(
            f"The workflow changed since release {release['semver']} was published: publish again to export the changes."
        )
    if check_only or not ready["ready"]:
        return ready
    output = Path(folder_paths.get_output_directory())
    result = export(release, contract, output, Path(folder_paths.get_input_directory()), also)
    if "folder" in result:
        dist = f"{result['folder']}/dist"
        result["downloads"] = [
            {"file": f, "url": f"/view?filename={f}&subfolder={dist}&type=output"}
            for f in result.get("files", [])
        ]
    return {**result, "todo": ready["todo"]}
