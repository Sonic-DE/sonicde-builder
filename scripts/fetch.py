#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""
Fetch active Git repositories for the SonicDE standalone builder.

Source acquisition lives here, not in ExternalProject. Completed checkouts are
updated without mutating HEAD/branch/index/worktree. Missing checkouts are
initialized, configured, fetched, and checked out per manifest metadata.
An empty init left by a failed initial fetch is completed on retry only when
it has no HEAD, local branches, index entries or worktree files to preserve.
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


def checkout_initial_ref(git_spec: dict, dest: Path) -> None:
    """Populate a newly fetched worktree; never reset an existing local branch."""
    ref = git_spec.get("ref", "")
    if not ref:
        raise RuntimeError(f"No initial checkout ref declared for {dest}")
    branch = git_spec.get("local_branch", "")
    if branch:
        run_git(str(dest), ["checkout", "-b", branch, ref])
    else:
        run_git(str(dest), ["checkout", "--detach", ref])


def require_empty_initial_checkout(dest: Path) -> None:
    """Only resume an empty init, never an orphan branch or local work."""
    if not (dest / ".git").is_dir():
        raise RuntimeError(f"Refusing initial-checkout recovery in linked worktree {dest}")
    if run_git(str(dest), ["for-each-ref", "--format=%(refname)", "refs/heads/"]).stdout.strip():
        raise RuntimeError(f"Refusing initial-checkout recovery: local branches exist in {dest}")
    if run_git(str(dest), ["ls-files", "--stage"]).stdout.strip():
        raise RuntimeError(f"Refusing initial-checkout recovery: index contains local work in {dest}")
    # status --porcelain omits ignored files; count every worktree entry instead.
    if any(entry.name != ".git" for entry in dest.iterdir()):
        raise RuntimeError(f"Refusing initial-checkout recovery: local files exist in {dest}")


def update_missing_submodules(pkg_name: str, dest: Path) -> None:
    """Initialize absent submodules recursively without moving existing ones."""
    modules = dest / ".gitmodules"
    if not modules.is_file():
        return
    configured = run_git(
        str(dest), ["config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        check=False,
    )
    if configured.returncode not in (0, 1):
        raise RuntimeError(f"Unable to read submodules in {dest}: {configured.stderr}")
    for line in configured.stdout.splitlines():
        _, separator, relative = line.partition(" ")
        if not separator or not relative:
            continue
        status = run_git(str(dest), ["submodule", "status", "--", relative], check=False)
        if status.returncode != 0:
            raise RuntimeError(f"Unable to inspect submodule {relative} in {dest}: {status.stderr}")
        if not status.stdout or status.stdout.startswith("-"):
            print(f"[{pkg_name}] initializing submodule {relative}")
            run_git(str(dest), ["submodule", "update", "--init", "--recursive", "--", relative])
        else:
            # Preserve this submodule's HEAD and worktree, but initialize any
            # missing nested submodules recorded by that existing commit.
            update_missing_submodules(pkg_name, dest / relative)


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
        # Fetch tags explicitly so a manifest's tagOpt (commonly --no-tags to
        # avoid implicit tag following) cannot prevent fetch-all from updating
        # the repository's tags.
        fetch_args = ["fetch", "--tags"]
        if rname == "upstream":
            # Upstream history may be rewritten. Keep its remote-tracking refs
            # synchronized even when the update is not a fast-forward.
            fetch_args.append("--force")
        if depth:
            fetch_args += ["--depth", str(depth)]
        fetch_args.append(rname)
        # explicit refspecs
        for refspec in (rdata.get("fetch") or []):
            fetch_args.append(refspec)
        print(f"[{pkg_name}] fetching {rname} "
              f"({redact_url(rdata.get('url', ''))})")
        run_git(repo_dir, fetch_args)

    checkout_initial_ref(git_spec, dest)
    update_missing_submodules(pkg_name, dest)


def fetch_existing(pkg_name: str, git_spec: dict, dest: Path,
                   dry_run: bool = False) -> None:
    """Fetch without changing checked-out work; resume a pristine incomplete init."""
    if dry_run:
        print(f"[{pkg_name}] would fetch existing at {dest}")
        return

    repo_dir = str(dest)
    needs_initial_checkout = run_git(
        repo_dir, ["rev-parse", "--verify", "HEAD^{commit}"], check=False
    ).returncode != 0
    if needs_initial_checkout:
        require_empty_initial_checkout(dest)
        print(f"[{pkg_name}] resuming incomplete initial fetch (no checked-out commit)")
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
        fetch_args = ["fetch", "--prune", "--tags"]
        if rname == "upstream":
            fetch_args.append("--force")
        if depth:
            fetch_args += ["--depth", str(depth)]
        fetch_args.append(rname)
        for refspec in (rdata.get("fetch") or []):
            fetch_args.append(refspec)
        print(f"[{pkg_name}] fetching {rname}")
        run_git(repo_dir, fetch_args)

    if needs_initial_checkout:
        # Recheck after network operations before populating any files.
        require_empty_initial_checkout(dest)
        checkout_initial_ref(git_spec, dest)

    update_missing_submodules(pkg_name, dest)


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
