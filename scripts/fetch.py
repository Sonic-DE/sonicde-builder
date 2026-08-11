#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""
Fetch active Git repositories for the SonicDE standalone builder.

Source acquisition lives here, not in ExternalProject.  Existing checkouts are
updated without mutating HEAD/branch/index/worktree.  Missing checkouts are
initialized, configured, fetched, and checked out per manifest metadata.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def redact_url(url: str) -> str:
    """Redact credentials from git URLs for logging."""
    # redact user:pass@ and tokens in https URLs
    return re.sub(r"(https?://)[^@/]+@", r"\1***@", url)


def run_git(cwd: str, args: list[str], check: bool = True,
            retries: int = 3) -> subprocess.CompletedProcess:
    for attempt in range(1, retries + 1):
        r = subprocess.run(["git"] + args, cwd=cwd,
                            capture_output=True, text=True)
        if r.returncode == 0 or not check:
            return r
        if attempt < retries:
            print(f"  retry {attempt}/{retries}: {' '.join(args[:3])}...",
                  file=sys.stderr)
    if check:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}:\n{r.stderr}")
    return r


def is_git_repo(path: Path) -> bool:
    return path.is_dir() and (path / ".git").exists()


def checkout_path(model: dict, pkg_name: str) -> Path:
    """Compute the checkout path for a package."""
    source_root = Path(model["source_root"])
    return source_root / pkg_name


def fetch_missing(pkg_name: str, git_spec: dict, dest: Path,
                  dry_run: bool = False) -> None:
    """Initialize and fetch a new checkout."""
    if dry_run:
        print(f"[{pkg_name}] would clone to {dest}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[{pkg_name}] initializing {dest}")

    run_git(str(dest.parent), ["init", dest.name])
    repo_dir = str(dest)

    # configure remotes FIRST (git remote add), then set remaining git config.
    # Setting remote.origin.tagOpt before adding the origin remote creates the
    # remote.origin config section, causing "remote origin already exists".
    for rname, rdata in (git_spec.get("remotes") or {}).items():
        url = rdata.get("url", "")
        if not url:
            continue
        run_git(repo_dir, ["remote", "add", rname, url])
        if rdata.get("tagopt"):
            run_git(repo_dir, ["config", f"remote.{rname}.tagOpt",
                               rdata["tagopt"]])
        # configure fetch refspecs
        for refspec in (rdata.get("fetch") or []):
            run_git(repo_dir, ["config", "--add",
                               f"remote.{rname}.fetch", refspec])

    # set remaining git config (including remote.* keys now that remotes exist)
    for key, val in (git_spec.get("config") or {}).items():
        run_git(repo_dir, ["config", key, val])

    # fetch each remote
    for rname, rdata in (git_spec.get("remotes") or {}).items():
        depth = rdata.get("depth", 0)
        fetch_args = ["fetch"]
        if depth:
            fetch_args += ["--depth", str(depth)]
        fetch_args.append(rname)
        # explicit refspecs
        for refspec in (rdata.get("fetch") or []):
            fetch_args.append(refspec)
        print(f"[{pkg_name}] fetching {rname} "
              f"({redact_url(rdata.get('url', ''))})")
        run_git(repo_dir, fetch_args)

    # checkout the ref
    ref = git_spec.get("ref", "")
    local_branch = git_spec.get("local_branch", "")
    if local_branch:
        # create local branch from the ref
        run_git(repo_dir, ["checkout", "-B", local_branch, ref])
    else:
        run_git(repo_dir, ["checkout", ref])


def fetch_existing(pkg_name: str, git_spec: dict, dest: Path,
                   dry_run: bool = False) -> None:
    """Update an existing checkout without mutating HEAD/index/worktree."""
    if dry_run:
        print(f"[{pkg_name}] would fetch existing at {dest}")
        return

    repo_dir = str(dest)
    print(f"[{pkg_name}] updating {dest}")

    # repair declared remote URLs and config
    for rname, rdata in (git_spec.get("remotes") or {}).items():
        url = rdata.get("url", "")
        # check if remote exists
        r = run_git(repo_dir, ["remote"], check=False)
        existing = r.stdout.split()
        if rname in existing:
            # verify/repair URL
            run_git(repo_dir, ["remote", "set-url", rname, url])
        else:
            run_git(repo_dir, ["remote", "add", rname, url])
        if rdata.get("tagopt"):
            run_git(repo_dir, ["config", f"remote.{rname}.tagOpt",
                               rdata["tagopt"]])

    # apply git config
    for key, val in (git_spec.get("config") or {}).items():
        if key.startswith("remote."):
            continue  # already handled above
        run_git(repo_dir, ["config", key, val])

    # fetch/prune declared remotes
    for rname, rdata in (git_spec.get("remotes") or {}).items():
        depth = rdata.get("depth", 0)
        fetch_args = ["fetch", "--prune"]
        if depth:
            fetch_args += ["--depth", str(depth)]
        fetch_args.append(rname)
        for refspec in (rdata.get("fetch") or []):
            fetch_args.append(refspec)
        print(f"[{pkg_name}] fetching {rname}")
        run_git(repo_dir, fetch_args)


def validate_existing(pkg_name: str, git_spec: dict,
                      dest: Path) -> bool:
    """Verify the existing checkout is a git repo with expected origin."""
    if not is_git_repo(dest):
        print(f"[{pkg_name}] ERROR: {dest} is not a git repository",
              file=sys.stderr)
        return False
    # verify origin URL matches
    r = run_git(str(dest), ["remote", "get-url", "origin"], check=False)
    expected_origin = ""
    for rname, rdata in (git_spec.get("remotes") or {}).items():
        if rname == "origin":
            expected_origin = rdata.get("url", "")
            break
    if expected_origin and r.returncode == 0:
        actual = r.stdout.strip()
        if actual != expected_origin:
            print(f"[{pkg_name}] WARNING: origin URL mismatch: "
                  f"expected {redact_url(expected_origin)}, "
                  f"got {redact_url(actual)}", file=sys.stderr)
    return True


def fetch_all(model: dict, dry_run: bool = False,
              only: list[str] | None = None) -> int:
    """Fetch all active git packages in topological order."""
    topo = model.get("topo_order", [])
    packages = model.get("packages", {})
    git_pkgs = [(name, packages[name]) for name in topo
                if packages[name].get("git")]

    if only:
        # filter to requested packages and their transitive prerequisites
        wanted = set()
        reverse = model.get("reverse", {})
        stack = list(only)
        while stack:
            n = stack.pop()
            if n in wanted:
                continue
            wanted.add(n)
            # add prerequisites (this package depends on them)
            for e in model.get("edges", []):
                if e["dependent"] == n:
                    stack.append(e["prerequisite"])
        git_pkgs = [(n, p) for n, p in git_pkgs if n in wanted]

    errors = 0
    for pkg_name, pkg in git_pkgs:
        git_spec = pkg["git"]
        dest = checkout_path(model, pkg_name)
        try:
            if not dest.exists() or not is_git_repo(dest):
                fetch_missing(pkg_name, git_spec, dest, dry_run)
            else:
                if not validate_existing(pkg_name, git_spec, dest):
                    errors += 1
                    continue
                fetch_existing(pkg_name, git_spec, dest, dry_run)
        except Exception as e:
            print(f"[{pkg_name}] FAILED: {e}", file=sys.stderr)
            errors += 1
    return errors


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Fetch SonicDE repositories")
    ap.add_argument("-model", default="state/model.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", action="append", default=[],
                    help="fetch only these packages and their prerequisites")
    args = ap.parse_args(argv)

    with open(args.model) as f:
        model = json.load(f)

    only = args.only or None
    errs = fetch_all(model, dry_run=args.dry_run, only=only)
    if errs:
        print(f"\n{errs} fetch failures", file=sys.stderr)
        return 1
    print("fetch complete")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
