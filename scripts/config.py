#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""
Configuration parser for the SonicDE standalone builder.

Parses only the YAML fields used by the active SonicDE solution, resolves the
dependency closure, and emits a normalized model used by fetch/build/graph/
autopick commands.  Unsupported active features are rejected explicitly.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Supported field whitelist (per active SonicDE usage)
# ---------------------------------------------------------------------------

PACKAGE_FIELDS = {
    "sources", "buildsystem", "build-depends", "depends", "provides",
    "type", "pkg-config", "platform-packages", "cmake-extra-args",
    "master_branch", "ref", "name", "@filename", "@basename", "@slug",
    "@PROJECT", "@SOLUTION", "source-dir", "install-prefix", "parallel",
    "@statdir", "@builddir", "@destdir", "@binary-tarball", "@binary-image",
    "build",  # structured build config (e.g. build.cmake.args)
}

GIT_FIELDS = {
    "ref", "remotes", "url", "depth", "fetch", "local-branch",
    "config", "post-checkout-cmd", "force-checkout", "tagopt",
}

SUPPORTED_BUILDSYSTEMS = {"cmake", "none"}

# Interpolation: ${name}, ${@basename}, ${@PROJECT::...}, ${@SOLUTION::...}
_VAR_RE = re.compile(r"\$\{([^}]+)\}")


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

def _resolve_key(key: str, scopes: list[dict[str, str]]) -> Any:
    """Resolve a ${...} key against a chain of scopes (project, solution)."""
    # Support @PROJECT::name and @SOLUTION::name prefixes
    if key.startswith("@PROJECT::"):
        sub = key[len("@PROJECT::"):]
        for sc in scopes:
            v = _lookup_nested(sc, sub)
            if v is not None:
                return v
        return None
    if key.startswith("@SOLUTION::"):
        sub = key[len("@SOLUTION::"):]
        for sc in scopes:
            v = _lookup_nested(sc, sub)
            if v is not None:
                return v
        return None
    # try each scope in order
    for sc in scopes:
        v = _lookup_nested(sc, key)
        if v is not None:
            return v
    return None


def _lookup_nested(scope: dict, key: str) -> Any:
    """Look up a key, supporting :: separators for nested dict access."""
    if key in scope:
        return scope[key]
    parts = key.split("::")
    cur: Any = scope
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def interpolate(value: Any, scopes: list[dict[str, str]]) -> Any:
    """Recursively interpolate ${...} tokens using a chain of scopes."""
    if isinstance(value, str):
        # If the entire string is a single ${...}, return the raw resolved
        # value (which may be a list or dict) instead of stringifying it.
        m = _VAR_RE.fullmatch(value)
        if m:
            v = _resolve_key(m.group(1), scopes)
            if v is not None:
                return v
            return value
        def repl(m: re.Match) -> str:
            key = m.group(1)
            v = _resolve_key(key, scopes)
            if v is not None:
                return str(v)
            return m.group(0)
        return _VAR_RE.sub(repl, value)
    if isinstance(value, list):
        return [interpolate(v, scopes) for v in value]
    if isinstance(value, dict):
        return {k: interpolate(v, scopes) for k, v in value.items()}
    return value


def has_unresolved(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_VAR_RE.search(value))
    if isinstance(value, list):
        return any(has_unresolved(v) for v in value)
    if isinstance(value, dict):
        return any(has_unresolved(v) for v in value.values())
    return False


def collect_unresolved_paths(value: Any, path: str = "") -> list[str]:
    if isinstance(value, str):
        if _VAR_RE.search(value):
            return [path or "<root>"]
        return []
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out += collect_unresolved_paths(v, f"{path}[{i}]")
        return out
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out += collect_unresolved_paths(v, f"{path}.{k}" if path else k)
        return out
    return []


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GitRemote:
    name: str
    url: str = ""
    depth: int = 0
    fetch: list[str] = field(default_factory=list)
    tagopt: str = ""


@dataclass
class GitSpec:
    ref: str = ""
    local_branch: str = ""
    remotes: dict[str, GitRemote] = field(default_factory=dict)
    config: dict[str, str] = field(default_factory=dict)
    post_checkout_cmd: list[str] = field(default_factory=list)
    force_checkout: bool = False


@dataclass
class Package:
    name: str  # namespace/path, e.g. sonicde/sonic-screenies
    manifest_path: str
    raw: dict[str, Any]
    buildsystem: str = ""
    ptype: str = ""
    depends: list[str] = field(default_factory=list)
    build_depends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    pkg_config: list[str] = field(default_factory=list)
    platform_packages: dict[str, str] = field(default_factory=dict)
    cmake_extra_args: list[str] = field(default_factory=list)
    git: GitSpec | None = None
    master_branch: str = ""
    ref: str = ""


@dataclass
class Edge:
    dependent: str
    prerequisite: str
    kind: str  # "depends" or "build-depends"


@dataclass
class Closure:
    packages: dict[str, Package] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    reverse: dict[str, list[str]] = field(default_factory=dict)
    topo_order: list[str] = field(default_factory=list)
    reasons: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class Project:
    def __init__(self, root: str | Path, solution_file: str | Path,
                 solution_defines: dict[str, str] | None = None,
                 project_defines: dict[str, str] | None = None):
        self.root = Path(root).resolve()
        self.solution_file = Path(solution_file).resolve()
        self.solution_defines = solution_defines or {}
        self.project_defines = project_defines or {}

        self.packages: dict[str, Package] = {}
        self.providers: dict[str, list[str]] = defaultdict(list)
        self.solution: dict[str, Any] = {}
        self.package_defaults: dict[str, Any] = {}
        self.package_mapping: dict[str, str] = {}

        self.scope: dict[str, str] = {}        # project scope
        self.solution_scope: dict[str, str] = {}  # solution scope
        self._resolving: set[str] = set()

        self._init_project_scope()

    # -- scope setup --------------------------------------------------------

    def _init_project_scope(self) -> None:
        self.scope["@rootdir"] = str(self.root)
        self.scope["@homedir"] = os.path.expanduser("~")
        # machine triple via gcc -dumpmachine, fallback to x86_64
        try:
            import subprocess
            m = subprocess.run(["gcc", "-dumpmachine"], capture_output=True,
                               text=True, timeout=5)
            self.scope["@machine"] = (m.stdout.strip() or "x86_64")
        except Exception:
            self.scope["@machine"] = "x86_64"
        self.scope["@workdir"] = str(self.root / "state")
        self.scope["@sourceroot"] = str(self.root / "src")
        self.scope["@installprefix"] = ""  # filled after solution load

    # -- loading ------------------------------------------------------------

    def load(self) -> None:
        # 1. apply project defines
        for k, v in self.project_defines.items():
            self.scope[k] = v

        # 2. load solution
        with open(self.solution_file) as f:
            raw_sol = yaml.safe_load(f) or {}
        if not isinstance(raw_sol, dict):
            raise ConfigError(f"solution is not a mapping: {self.solution_file}")
        self.solution = raw_sol

        # 3. apply solution defines
        for k, v in self.solution_defines.items():
            self.solution[k] = v

        # Build solution scope: flat scalars + full nested dict for :: lookups
        self.solution_scope = {k: str(v) for k, v in self.solution.items()
                               if isinstance(v, (str, int, float))}
        # also keep full solution for nested :: access
        self.solution_scope_full = dict(self.solution)

        # interpolate solution-level scalars against project scope
        self.solution = interpolate(self.solution, [self.scope, self.solution_scope, self.solution_scope_full])

        # refresh solution scope after interpolation
        self.solution_scope = {k: str(v) for k, v in self.solution.items()
                               if isinstance(v, (str, int, float))}
        self.solution_scope_full = dict(self.solution)

        # extract install prefix
        ip = self.solution.get("install-prefix", "")
        if isinstance(ip, str):
            ip = interpolate(ip, [self.scope, self.solution_scope])
            if not _VAR_RE.search(ip):
                self.scope["install-prefix"] = ip
                self.scope["@installprefix"] = ip
            else:
                raise ConfigError(f"unresolved install-prefix: {ip}")

        # package mapping
        pm = self.solution.get("package-mapping") or {}
        if isinstance(pm, dict):
            self.package_mapping = {str(k): str(v) for k, v in pm.items()}

        # package defaults
        pd = self.solution.get("package-defaults") or {}
        if isinstance(pd, dict):
            self.package_defaults = interpolate(pd, [self.scope, self.solution_scope, self.solution_scope_full])

        # 4. load package manifests
        pkg_roots = self.solution.get("packages") or []
        if not isinstance(pkg_roots, list):
            raise ConfigError("solution 'packages' must be a list")
        for root_expr in pkg_roots:
            root_str = interpolate(root_expr, [self.scope, self.solution_scope, self.solution_scope_full])
            if has_unresolved(root_str):
                raise ConfigError(f"unresolved package root: {root_expr}")
            proot = Path(root_str)
            if not proot.is_dir():
                raise ConfigError(f"package root not found: {proot}")
            self._load_packages(proot)

        # 5. apply package defaults
        self._apply_defaults()

        # 6. validate supported fields
        self._validate_fields()

    def _load_packages(self, proot: Path, prefix: str = "") -> None:
        for entry in sorted(proot.iterdir()):
            if entry.is_dir():
                sub = f"{prefix}/{entry.name}" if prefix else entry.name
                self._load_packages(entry, sub)
            elif entry.suffix in (".yaml", ".yml"):
                ident = f"{prefix}/{entry.stem}" if prefix else entry.stem
                if ident in self.packages:
                    raise ConfigError(f"duplicate package identity: {ident}")
                with open(entry) as f:
                    raw = yaml.safe_load(f) or {}
                if not isinstance(raw, dict):
                    raise ConfigError(f"{entry}: not a mapping")
                pkg = Package(
                    name=ident,
                    manifest_path=str(entry),
                    raw=raw,
                )
                self._populate(pkg)
                self.packages[ident] = pkg
                for prov in pkg.provides:
                    self.providers[prov].append(ident)

    def _populate(self, pkg: Package) -> None:
        r = pkg.raw
        # Build per-package scope: project + solution + package-local
        pkg_scope = dict(self.scope)
        pkg_scope["@basename"] = pkg.name.split("/")[-1]
        pkg_scope["name"] = pkg.name
        pkg_scope["master_branch"] = str(r.get("master_branch", ""))
        pkg_scope["ref"] = str(r.get("ref", ""))
        # also add package defaults that are scalars (sonicde_git, etc.)
        for k, v in self.package_defaults.items():
            if isinstance(v, (str, int, float)):
                pkg_scope[k] = str(v)
        scopes = [pkg_scope, self.solution_scope, self.solution_scope_full, self.scope]
        r = interpolate(r, scopes)
        pkg.raw = r
        pkg.buildsystem = r.get("buildsystem", "")
        pkg.ptype = r.get("type", "")
        dep = r.get("depends") or []
        if isinstance(dep, str): dep = [dep]
        pkg.depends = list(dep)
        bdep = r.get("build-depends") or []
        if isinstance(bdep, str): bdep = [bdep]
        pkg.build_depends = list(bdep)
        prov = r.get("provides") or []
        if isinstance(prov, str): prov = [prov]
        pkg.provides = list(prov)
        pc = r.get("pkg-config") or []
        if isinstance(pc, str): pc = [pc]
        pkg.pkg_config = list(pc)
        pp = r.get("platform-packages") or {}
        if isinstance(pp, dict):
            pkg.platform_packages = {str(k): str(v) for k, v in pp.items()}
        cea = r.get("cmake-extra-args") or []
        if isinstance(cea, str): cea = [cea]
        pkg.cmake_extra_args = list(cea)
        # also pull build.cmake.args if present
        build_cfg = r.get("build") or {}
        if isinstance(build_cfg, dict):
            cmake_cfg = build_cfg.get("cmake") or {}
            if isinstance(cmake_cfg, dict):
                args = cmake_cfg.get("args") or []
                if isinstance(args, str): args = [args]
                pkg.cmake_extra_args += list(args)
        pkg.master_branch = r.get("master_branch", "")
        pkg.ref = r.get("ref", "")

        git_raw = (r.get("sources") or {}).get("git")
        if git_raw:
            pkg.git = self._parse_git(git_raw, pkg)

    def _parse_git(self, g: dict[str, Any], pkg: Package) -> GitSpec:
        unknown = set(g.keys()) - GIT_FIELDS
        if unknown:
            raise ConfigError(
                f"{pkg.name}: unsupported git fields: {sorted(unknown)}")

        spec = GitSpec()
        spec.ref = str(g.get("ref", ""))
        spec.local_branch = str(g.get("local-branch", ""))
        spec.force_checkout = bool(g.get("force-checkout", False))
        pcc = g.get("post-checkout-cmd") or []
        if isinstance(pcc, str): pcc = [pcc]
        spec.post_checkout_cmd = list(pcc)

        cfg = g.get("config") or {}
        if isinstance(cfg, dict):
            spec.config = {str(k): str(v) for k, v in cfg.items()}

        remotes_raw = g.get("remotes") or {}
        # also allow top-level url/depth/fetch as an implicit "origin"
        # but only when an explicit url is present (depth/fetch alone are
        # per-remote fields in named-remote manifests)
        if "url" in g:
            if "origin" in remotes_raw:
                raise ConfigError(
                    f"{pkg.name}: both implicit origin and named remotes")
            remotes_raw = {"origin": {
                "url": g.get("url", ""),
                "depth": g.get("depth", 0),
                "fetch": g.get("fetch") or [],
            }}
        if not isinstance(remotes_raw, dict):
            raise ConfigError(f"{pkg.name}: remotes must be a mapping")
        # top-level depth/fetch act as defaults for named remotes
        top_depth = g.get("depth", None)
        top_fetch = g.get("fetch", None)
        for rname, rdata in (remotes_raw or {}).items():
            if not isinstance(rdata, dict):
                raise ConfigError(f"{pkg.name}: remote {rname} not a mapping")
            rdepth = rdata.get("depth", top_depth if top_depth is not None else 0)
            rfetch = rdata.get("fetch", top_fetch or [])
            # fetch may be a string or list; normalize to list
            if isinstance(rfetch, str):
                rfetch = [rfetch]
            elif not isinstance(rfetch, list):
                rfetch = []
            spec.remotes[rname] = GitRemote(
                name=rname,
                url=str(rdata.get("url", "")),
                depth=int(rdepth or 0),
                fetch=list(rfetch),
                tagopt=str(rdata.get("tagopt", "")),
            )
        if not spec.remotes:
            raise ConfigError(f"{pkg.name}: git source has no remotes")
        return spec

    def _apply_defaults(self) -> None:
        # Package defaults are used for interpolation scope only, not stored
        # in pkg.raw (which would make them appear as unknown package fields).
        # _populate already pulls package_defaults into the per-package scope.
        # Just re-populate to ensure interpolation is applied consistently.
        for pkg in self.packages.values():
            self._populate(pkg)

    def _validate_fields(self) -> None:
        for pkg in self.packages.values():
            unknown = set(pkg.raw.keys()) - PACKAGE_FIELDS
            if unknown:
                raise ConfigError(
                    f"{pkg.name}: unsupported package fields: {sorted(unknown)}")

    # -- resolution ---------------------------------------------------------

    def resolve_name(self, name: str) -> str:
        """Apply mapping, then exact, then unique provider."""
        mapped = self.package_mapping.get(name, name)
        if mapped in self.packages:
            return mapped
        ps = self.providers.get(name, [])
        if len(ps) == 1:
            return ps[0]
        if not ps:
            raise ConfigError(f"cannot resolve dependency: {name}")
        raise ConfigError(
            f"ambiguous provider for {name}: {ps}")

    def build_closure(self) -> Closure:
        cl = Closure()
        build_list = self.solution.get("build") or []
        if not isinstance(build_list, list):
            raise ConfigError("solution 'build' must be a list")

        # BFS with reason tracking
        queue: deque[tuple[str, str]] = deque()
        for b in build_list:
            queue.append((b, "<solution::build>"))

        in_list = set(build_list)
        while queue:
            raw_name, reason = queue.popleft()
            try:
                ident = self.resolve_name(raw_name)
            except ConfigError as e:
                raise ConfigError(f"{reason} -> {e}") from e

            if ident in cl.packages:
                cl.reasons[ident].append(reason)
                continue
            cl.packages[ident] = self.packages[ident]
            cl.reasons[ident] = [reason]

            pkg = self.packages[ident]
            for dep in pkg.build_depends:
                try:
                    did = self.resolve_name(dep)
                except ConfigError as e:
                    raise ConfigError(
                        f"{ident} build-depends -> {e}") from e
                cl.edges.append(Edge(ident, did, "build-depends"))
                cl.reverse.setdefault(did, []).append(ident)
                queue.append((dep, f"{ident} build-depends"))
            for dep in pkg.depends:
                try:
                    did = self.resolve_name(dep)
                except ConfigError as e:
                    raise ConfigError(
                        f"{ident} depends -> {e}") from e
                cl.edges.append(Edge(ident, did, "depends"))
                cl.reverse.setdefault(did, []).append(ident)
                queue.append((dep, f"{ident} depends"))

        # cycle detection + topological order
        self._topo(cl)

        # validate active closure constraints
        self._validate_closure(cl)

        return cl

    def _topo(self, cl: Closure) -> None:
        # Kahn's algorithm with cycle detection via remaining set
        indeg: dict[str, int] = {n: 0 for n in cl.packages}
        adj: dict[str, list[str]] = {n: [] for n in cl.packages}
        for e in cl.edges:
            adj[e.prerequisite].append(e.dependent)
            indeg[e.dependent] += 1

        ready = sorted(n for n, d in indeg.items() if d == 0)
        order: list[str] = []
        remaining = set(cl.packages)
        while ready:
            n = ready.pop(0)
            order.append(n)
            remaining.discard(n)
            for m in sorted(adj[n]):
                indeg[m] -= 1
                if indeg[m] == 0:
                    # insert in sorted position
                    ready.append(m)
                    ready.sort()

        if remaining:
            # find a cycle in remaining
            cycle = self._find_cycle(remaining, adj)
            raise ConfigError(
                f"dependency cycle detected: {' -> '.join(cycle)}")

        cl.topo_order = order

    def _find_cycle(self, remaining: set[str],
                    adj: dict[str, list[str]]) -> list[str]:
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def dfs(n: str) -> list[str] | None:
            visited.add(n)
            stack.append(n)
            on_stack.add(n)
            for m in adj.get(n, []):
                if m not in remaining:
                    continue
                if m in on_stack:
                    idx = stack.index(m)
                    return stack[idx:] + [m]
                if m not in visited:
                    found = dfs(m)
                    if found:
                        return found
            stack.pop()
            on_stack.discard(n)
            return None

        for n in sorted(remaining):
            if n not in visited:
                c = dfs(n)
                if c:
                    return c
        return list(remaining)

    def _validate_closure(self, cl: Closure) -> None:
        for ident, pkg in cl.packages.items():
            # reject active qt6/* source packages
            if ident.startswith("qt6/"):
                raise ConfigError(
                    f"active Qt source package not supported: {ident}")
            # reject unsupported build systems on active buildable packages
            if pkg.ptype not in ("system", "fetchonly"):
                if pkg.buildsystem and pkg.buildsystem not in SUPPORTED_BUILDSYSTEMS:
                    raise ConfigError(
                        f"{ident}: unsupported buildsystem '{pkg.buildsystem}'")
            # reject unused git features if active
            if pkg.git:
                if pkg.git.force_checkout:
                    raise ConfigError(
                        f"{ident}: force-checkout not supported")
                if pkg.git.post_checkout_cmd:
                    raise ConfigError(
                        f"{ident}: post-checkout-cmd not supported")

    # -- serialization -------------------------------------------------------

    def to_model(self, cl: Closure) -> dict[str, Any]:
        pkgs = {}
        for ident, pkg in cl.packages.items():
            entry: dict[str, Any] = {
                "name": ident,
                "manifest": pkg.manifest_path,
                "buildsystem": pkg.buildsystem,
                "type": pkg.ptype,
                "depends": pkg.depends,
                "build_depends": pkg.build_depends,
                "provides": pkg.provides,
                "pkg_config": pkg.pkg_config,
                "cmake_extra_args": pkg.cmake_extra_args,
                "reasons": cl.reasons.get(ident, []),
            }
            if pkg.git:
                entry["git"] = {
                    "ref": pkg.git.ref,
                    "local_branch": pkg.git.local_branch,
                    "remotes": {
                        n: {
                            "url": r.url, "depth": r.depth,
                            "fetch": r.fetch, "tagopt": r.tagopt,
                        } for n, r in pkg.git.remotes.items()
                    },
                    "config": pkg.git.config,
                }
            pkgs[ident] = entry

        return {
            "schema_version": 1,
            "root": str(self.root),
            "solution_file": str(self.solution_file),
            "install_prefix": self.scope.get("@installprefix", ""),
            "source_root": self.scope.get("@sourceroot", ""),
            "build_dir": str(self.root / "build"),
            "state_dir": str(self.root / "state"),
            "packages": pkgs,
            "edges": [
                {"dependent": e.dependent,
                 "prerequisite": e.prerequisite, "kind": e.kind}
                for e in cl.edges
            ],
            "topo_order": cl.topo_order,
            "reverse": dict(cl.reverse),
        }

    def write_model(self, cl: Closure, path: Path) -> None:
        model = self.to_model(cl)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(model, f, indent=2, sort_keys=True)
        os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Parse SonicDE config")
    ap.add_argument("-root", default=".")
    ap.add_argument("-solution", required=True)
    ap.add_argument("-solution-define", action="append", default=[])
    ap.add_argument("-project-define", action="append", default=[])
    ap.add_argument("-out", default="state/model.json")
    ap.add_argument("--closure-only", action="store_true")
    args = ap.parse_args(argv)

    sdef = {}
    for d in args.solution_define:
        k, _, v = d.partition("=")
        sdef[k] = v
    pdef = {}
    for d in args.project_define:
        k, _, v = d.partition("=")
        pdef[k] = v

    prj = Project(args.root, args.solution, sdef, pdef)
    prj.load()
    cl = prj.build_closure()

    print(f"closure: {len(cl.packages)} packages, {len(cl.edges)} edges")
    if args.closure_only:
        return 0

    prj.write_model(cl, Path(args.out))
    print(f"model written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
