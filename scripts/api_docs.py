#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate the SonicDE API reference (Markdown for sonicde.org) from every built library.

Every library that calls ecm_generate_qdoc() has prepare_docs_<target> Ninja targets in its build
directory. Their commands give the module's working directory, environment, qdoc, .qdocconf, include
list and Qt's documentation directory (which ECM asks Qt for), so this script runs QDoc with exactly
those, but with one shared index and output directory:

  1. qdoc --prepare (HTML, the only format that writes .index files) for every module;
  2. qdoc --generate (DocBook) for every module, with every module's index available, so links
     between libraries resolve;
  3. docbook2markdown6 for every module into one fresh Markdown directory (by default the one built
     in the sonic-frameworks-doctools build directory, else an installed one).

That directory then replaces api/ in the builder's sonic-docs checkout (src/sonicde/sonic-docs,
made by ./fetch sonic-docs), or the folder given with --output, which must be empty or hold a
previous reference (a toc.yaml). Committing and pushing sonic-docs is left to the caller. A failing
module is logged, named in the summary and left out; the exit code is 1 when any module failed.

Runs are incremental (state.json in the work directory): a module is prepared again only when its
repository's source changed, and generated and converted again when its source or the index of a
module it depends on changed. A different qdoc, Qt documentation or QDoc templates redo everything,
a different converter converts everything again; --full redoes everything.

Usage:
    api_docs.py [--repo NAME ...] [--output DIR] [--jobs N] [--full]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The sonic-docs checkout that ./fetch sonic-docs makes (config.py's @sourceroot is <root>/src).
DOCS_CHECKOUT = ROOT / "src" / "sonicde" / "sonic-docs"
QDOC_TIMEOUT = 900
MODULE_USE = ROOT / "config" / "qdoc" / "module-use.qdocinc"


@dataclass
class Module:
    """One ecm_generate_qdoc() module, as its prepare_docs_<target> command runs it."""

    repo: str
    target: str
    cwd: Path
    env: dict[str, str]
    qdoc: str
    qdocconf: Path
    includes: str
    #: ECM's index directories other than its own output: Qt's documentation directory first.
    index_dirs: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        # ECM's doc target name: the .qdocconf's name, e.g. "kconfigcore".
        return self.qdocconf.stem


@dataclass
class Result:
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def parse_command(repo: str, target: str, command: str) -> Module:
    """Reads ECM's prepare command: "cd DIR && cmake -E env K=V... qdoc ARGS"."""
    words = shlex.split(command)
    if len(words) < 4 or words[0] != "cd" or words[2] != "&&":
        raise ValueError(f"unexpected command for {target}: {command}")
    cwd = Path(words[1])
    rest = words[3:]
    env: dict[str, str] = {}
    if len(rest) > 2 and rest[1:3] == ["-E", "env"]:
        rest = rest[3:]
        while rest and "=" in rest[0] and not rest[0].startswith("-"):
            key, value = rest.pop(0).split("=", 1)
            env[key] = value
    if not rest:
        raise ValueError(f"no qdoc in the command for {target}")
    qdoc, args = rest[0], rest[1:]
    qdocconfs = [arg for arg in args if arg.endswith(".qdocconf")]
    includes = [arg[1:] for arg in args if arg.startswith("@")]
    if len(qdocconfs) != 1 or len(includes) != 1:
        raise ValueError(f"no single .qdocconf and include list in the command for {target}")

    def values(option: str) -> list[str]:
        return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg.lstrip("-") == option.lstrip("-")]

    # ECM's own output (-installdir, the build's .doc) is replaced by the shared index directory.
    own = set(values("installdir"))
    index_dirs = [path for path in values("indexdir") if path not in own]
    return Module(repo, target, cwd, env, qdoc, Path(qdocconfs[0]), includes[0], index_dirs)


def qt_docs_dir(modules: list[Module]) -> Path | None:
    """Qt's documentation directory, as ECM found it (ecm_query_qt(QT_INSTALL_DOCS))."""
    for module in modules:
        if module.index_dirs:
            return Path(module.index_dirs[0])
    return None


def overlay_templates(qt_docs: Path, target: Path) -> None:
    """Makes target a copy of Qt's QDoc templates, by symlinks, with SonicDE's module-use.qdocinc."""
    shutil.rmtree(target, ignore_errors=True)
    includes = target / "global" / "includes"
    includes.mkdir(parents=True)
    for entry in (qt_docs / "global").iterdir():
        if entry.name != "includes":
            (target / "global" / entry.name).symlink_to(entry)
    for entry in (qt_docs / "global" / "includes").iterdir():
        (includes / entry.name).symlink_to(entry)
    (includes / MODULE_USE.name).unlink(missing_ok=True)
    (includes / MODULE_USE.name).symlink_to(MODULE_USE)


def discover(build_root: Path, repos: list[str] | None = None) -> list[Module]:
    """Every module of every configured build under build_root (sorted by repo, then target)."""
    modules = []
    for build in sorted(path for path in build_root.iterdir() if (path / "build.ninja").is_file()):
        if repos and build.name not in repos:
            continue
        targets = subprocess.run(["ninja", "-C", str(build), "-t", "targets", "all"],
                                 capture_output=True, text=True, check=True).stdout
        names = sorted({line.split(":", 1)[0] for line in targets.splitlines()
                        if line.startswith("prepare_docs_")})
        for name in names:
            commands = subprocess.run(["ninja", "-C", str(build), "-t", "commands", name],
                                      capture_output=True, text=True, check=True).stdout.splitlines()
            modules.append(parse_command(build.name, name.removeprefix("prepare_docs_"), commands[-1]))
    return modules


def qdoc_command(module: Module, phase: str, work: Path) -> list[str]:
    """The qdoc command line for one pass, with the shared index and output directories."""
    index = work / "index"
    output = index if phase == "prepare" else work / "docbook"
    index_args = []
    for directory in [str(index), *module.index_dirs]:
        index_args += ["--indexdir", directory]
    return [
        module.qdoc, str(module.qdocconf), f"--{phase}",
        "--outputformat", "HTML" if phase == "prepare" else "DocBook",
        "-outputdir", str(output / module.name),
        *index_args,
        "-installdir", str(index),
        "-no-link-errors",
        f"@{module.includes}",
    ]


def run_pass(modules: list[Module], phase: str, work: Path, kde_docs: Path, jobs: int, result: Result) -> None:
    """Runs one QDoc pass over the modules that haven't failed yet."""
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    def run(module: Module) -> tuple[Module, str | None]:
        log = logs / f"{phase}-{module.name}.log"
        env = {**os.environ, **module.env, "KDE_DOCS": str(kde_docs)}
        try:
            with log.open("w") as out:
                code = subprocess.run(qdoc_command(module, phase, work), cwd=module.cwd, env=env,
                                      stdout=out, stderr=subprocess.STDOUT, timeout=QDOC_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            return module, f"qdoc --{phase} timed out after {QDOC_TIMEOUT} s (log {log})"
        except OSError as error:
            return module, f"qdoc --{phase} didn't run: {error}"
        return module, None if code == 0 else f"qdoc --{phase} exited {code} (log {log})"

    pending = [module for module in modules if module.name not in result.failed]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for module, error in pool.map(run, pending):
            if error:
                result.failed[module.name] = error
                print(f"FAILED {module.repo}/{module.name}: {error}", file=sys.stderr)


def convert(modules: list[Module], work: Path, converter: str, qt_docs: Path, result: Result) -> Path:
    """Converts the modules' DocBook into the shared Markdown directory, one module at a time
    (each run rewrites the shared toc.yaml, index page and all-members pages)."""
    markdown = work / "markdown"
    markdown.mkdir(parents=True, exist_ok=True)
    (work / "logs").mkdir(exist_ok=True)
    for module in modules:
        if module.name in result.failed:
            continue
        shutil.rmtree(markdown / module.name, ignore_errors=True)
        docbook = work / "docbook" / module.name
        log = work / "logs" / f"convert-{module.name}.log"
        # The prepare pass's index (named after the project, not always the module) names the
        # structs and unions, whose DocBook pages QDoc leaves untitled.
        index = sorted((work / "index" / module.name).glob("*.index"))
        index_args = ["--index", str(index[0])] if index else []
        with log.open("w") as out:
            code = subprocess.run([converter, "--module", module.name, "--qt-docs-dir", str(qt_docs), *index_args,
                                   str(docbook), str(markdown)], stdout=out, stderr=subprocess.STDOUT).returncode
        if code:
            result.failed[module.name] = f"docbook2markdown6 exited {code} (log {log})"
            print(f"FAILED {module.repo}/{module.name}: {result.failed[module.name]}", file=sys.stderr)
        else:
            result.ok.append(module.name)
    return markdown


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_digest(work: Path, name: str) -> str | None:
    """The hash of the module's .index files from the prepare pass; None without one."""
    files = sorted((work / "index" / name).glob("*.index"))
    return hashlib.sha256(b"".join(file.read_bytes() for file in files)).hexdigest() if files else None


def source_root(module: Module) -> Path:
    """The repository holding the module's .qdocconf, else the .qdocconf's directory."""
    top = subprocess.run(["git", "-C", str(module.qdocconf.parent), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return Path(top.stdout.strip()) if top.returncode == 0 else module.qdocconf.parent


def source_fingerprint(root: Path) -> str:
    """Changes whenever the source does: the git HEAD, uncommitted changes and untracked files'
    contents, or every file's contents outside git."""
    digest = hashlib.sha256()

    def git(*args: str) -> bytes:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True).stdout

    if (root / ".git").exists():
        digest.update(git("rev-parse", "HEAD"))
        digest.update(git("diff", "HEAD", "--binary"))
        untracked = git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        files = sorted(root / name.decode() for name in untracked if name)
    else:
        files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def qdocconf_depends(qdocconf: Path) -> set[str] | None:
    """The modules named by the .qdocconf's "depends" (continuation lines joined); None if unreadable."""
    try:
        text = qdocconf.read_text().replace("\\\n", " ")
    except OSError:
        return None
    names: set[str] = set()
    for match in re.finditer(r"^\s*depends\s*\+?=(.*)$", text, re.MULTILINE):
        names.update(match.group(1).split())
    return None if "*" in names else names


def tool_digest(program: str) -> str:
    path = shutil.which(program)
    return file_digest(Path(path)) if path else program


def load_state(work: Path) -> dict:
    try:
        return json.loads((work / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def stale(modules: list[Module], state: dict, sources: dict[str, str], work: Path) -> set[str]:
    """The modules whose source changed since the last run, or whose output is missing."""
    recorded = state.get("modules", {})
    return {module.name for module in modules
            if recorded.get(module.name, {}).get("source") != sources[module.repo]
            or index_digest(work, module.name) is None
            or not (work / "docbook" / module.name).is_dir()
            or not (work / "markdown" / module.name / "module.json").is_file()}


def dependents(modules: list[Module], state: dict, work: Path, done: set[str]) -> set[str]:
    """Modules not in done that depend on a module whose index differs from the recorded one."""
    recorded = state.get("modules", {})
    # Depends name a module by its folder or its index file (the QDoc project, e.g. modemmanagerqt).
    aliases: dict[str, str] = {}
    for module in modules:
        aliases[module.name] = module.name
        for file in (work / "index" / module.name).glob("*.index"):
            aliases[file.stem] = module.name
    changed = {module.name for module in modules
               if index_digest(work, module.name) != recorded.get(module.name, {}).get("index")}
    result = set()
    for module in modules:
        if module.name in done:
            continue
        names = qdocconf_depends(module.qdocconf)
        uses = changed if names is None else {aliases[name] for name in names if name in aliases}
        if uses & changed:
            result.add(module.name)
    return result


def replace_output(markdown: Path, output: Path) -> None:
    """Replaces output with the generated reference; refuses a folder that isn't one."""
    if output.exists():
        if any(output.iterdir()) and not (output / "toc.yaml").is_file():
            raise ValueError(f"{output} isn't empty and holds no toc.yaml; not replacing it")
        shutil.rmtree(output)
    shutil.copytree(markdown, output)


def default_converter(build_root: Path) -> str:
    """The converter built in this checkout (its run path finds the staged libraries), else an
    installed one."""
    built = build_root / "sonic-frameworks-doctools" / "bin" / "docbook2markdown6"
    return str(built) if built.is_file() else "docbook2markdown6"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the SonicDE API reference from every built library.")
    parser.add_argument("--repo", action="append", help="Only this repository (repeatable).")
    parser.add_argument("--output", type=Path, default=DOCS_CHECKOUT / "api",
                        help="Folder to replace with the reference (default: api/ in the builder's sonic-docs "
                             "checkout, src/sonicde/sonic-docs).")
    parser.add_argument("--build-root", type=Path, default=ROOT / "build" / "sonicde")
    parser.add_argument("--work", type=Path, default=ROOT / "state" / "api-docs",
                        help="Indexes, DocBook, Markdown and logs (default state/api-docs).")
    parser.add_argument("--kde-docs", type=Path,
                        help="QDoc templates for $KDE_DOCS in the .qdocconf files "
                             "(default: $KDE_DOCS, else Qt's with config/qdoc/module-use.qdocinc).")
    parser.add_argument("--converter", help="The docbook2markdown6 to run "
                                            "(default: the one built in this checkout, else an installed one).")
    parser.add_argument("--jobs", "-j", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--full", action="store_true", help="Redo every module, not only the changed ones.")
    args = parser.parse_args(argv)

    if not args.output.parent.is_dir():
        print(f"{args.output.parent} doesn't exist; run ./fetch sonic-docs first (or pass --output).", file=sys.stderr)
        return 1
    modules = discover(args.build_root, args.repo)
    if not modules:
        print("No ecm_generate_qdoc() modules found in the builds.", file=sys.stderr)
        return 1
    qt_docs = qt_docs_dir(modules)
    if qt_docs is None:
        print("The builds name no Qt documentation directory (ECM's QT_INSTALL_DOCS).", file=sys.stderr)
        return 1
    work = args.work
    chosen_docs = args.kde_docs or os.environ.get("KDE_DOCS")
    kde_docs = Path(chosen_docs) if chosen_docs else work.parent / f"{work.name}-templates"
    converter = args.converter or default_converter(args.build_root)
    print(f"Qt documentation: {qt_docs}; QDoc templates: {kde_docs}; converter: {converter}")
    names = [module.name for module in modules]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        print(f"Several modules share a name: {', '.join(duplicates)}", file=sys.stderr)
        return 1

    if not chosen_docs:
        overlay_templates(qt_docs, kde_docs)
    qdoc_key = {"qdoc": sorted({tool_digest(module.qdoc) for module in modules}),
                "qt_docs": str(qt_docs), "kde_docs": str(kde_docs)}
    if not chosen_docs:
        qdoc_key["module_use"] = file_digest(MODULE_USE)
    converter_key = tool_digest(converter)
    state = {} if args.full else load_state(work)
    if state.get("qdoc") != qdoc_key:
        shutil.rmtree(work, ignore_errors=True)
        state = {}
    reconvert = state.get("converter") != converter_key

    result = Result()
    print(f"{len(modules)} modules in {len({module.repo for module in modules})} repositories")
    roots = {module.repo: source_root(module) for module in modules}
    sources = {repo: source_fingerprint(root) for repo, root in roots.items()}
    prepare = stale(modules, state, sources, work)
    for name in prepare:
        for folder in ("index", "docbook"):
            shutil.rmtree(work / folder / name, ignore_errors=True)
    run_pass([module for module in modules if module.name in prepare], "prepare", work, kde_docs, args.jobs, result)
    generate = prepare | dependents(modules, state, work, prepare)
    for name in generate - prepare:
        shutil.rmtree(work / "docbook" / name, ignore_errors=True)
    run_pass([module for module in modules if module.name in generate], "generate", work, kde_docs, args.jobs, result)

    recorded = state.get("modules", {})
    names = {module.name for module in modules}
    # Modules no longer built, and failed ones, leave the reference (a --repo run keeps the others).
    gone = set(recorded) - names if not args.repo else set()
    for name in gone | set(result.failed):
        recorded.pop(name, None)
        shutil.rmtree(work / "markdown" / name, ignore_errors=True)
    to_convert = names if reconvert else set(generate)
    if gone and not to_convert:
        to_convert = {modules[0].name}  # one conversion rewrites the contents without them
    markdown = convert([module for module in modules if module.name in to_convert], work, converter, qt_docs, result)
    for name in result.failed:
        recorded.pop(name, None)
        shutil.rmtree(markdown / name, ignore_errors=True)
    for module in modules:
        if module.name not in result.failed:
            recorded[module.name] = {"source": sources[module.repo], "index": index_digest(work, module.name)}
    (work / "state.json").write_text(json.dumps({"qdoc": qdoc_key, "converter": converter_key, "modules": recorded},
                                                indent=1, sort_keys=True))

    pages = sum(1 for _ in markdown.rglob("*.md"))
    print(f"{len(result.ok)} modules converted, {len(names) - len(to_convert)} unchanged, {pages} pages in {markdown}")
    if result.failed:
        print(f"{len(result.failed)} modules failed:")
        for name in sorted(result.failed):
            print(f"  {name}: {result.failed[name]}")
    replace_output(markdown, args.output)
    print(f"Replaced {args.output}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
