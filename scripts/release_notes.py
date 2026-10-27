#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""List SonicDE's own changes since each repository's last SonicDE release.

Read-only: it only runs read-only git commands and prints tab-separated rows,
which are the input for writing the repositories' RELEASE-NOTES.md before a
release is tagged.

A SonicDE release tag is an unprefixed numeric tag (6.8.0, 6.31.0.1) that is
not on any upstream branch; tags starting with "v" are upstream releases. A
change is SonicDE's own when its patch is not on the upstream default branch
and its subject matches no upstream commit. Repositories without an upstream
remote no longer track one, so every commit counts.

Usage:
    release_notes.py core|frameworks|system [--root DIR]
    release_notes.py --repo NAME [--repo NAME ...] [--root DIR]

Output columns: repo, last release, commit, date, author, subject. A
repository with no changes prints one row with "-" for the commit columns.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

RELEASE_TAG = re.compile(r"^\d+(?:\.\d+)+$")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _ref(repo: Path, *candidates: str) -> str | None:
    for candidate in candidates:
        result = subprocess.run(["git", "rev-parse", "-q", "--verify", f"{candidate}^{{commit}}"],
                                cwd=str(repo), capture_output=True, text=True)
        if result.returncode == 0:
            return candidate
    return None


def _version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.split("."))


def _upstream_branches(repo: Path) -> list[str]:
    refs = _git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes/upstream").split()
    return [ref for ref in refs if not ref.endswith("/HEAD")]


def last_release(repo: Path, target: str, upstream: list[str]) -> str | None:
    """The newest SonicDE release tag contained in *target*."""
    tags = [tag for tag in _git(repo, "tag", "--merged", target).split() if RELEASE_TAG.match(tag)]
    ours = []
    for tag in tags:
        if upstream:
            on_upstream = _git(repo, "for-each-ref", "--contains", f"{tag}^{{commit}}",
                               "--format=%(refname)", *upstream).strip()
            if on_upstream:
                continue
        ours.append(tag)
    return max(ours, key=_version_key) if ours else None


def changes(repo: Path) -> tuple[str | None, list[tuple[str, str, str, str]]]:
    """Return the last SonicDE release and SonicDE's commits since it, oldest first."""
    target = _ref(repo, "refs/remotes/origin/master", "HEAD")
    if target is None:
        raise ValueError("no origin/master or HEAD to read")
    upstream = _upstream_branches(repo)
    upstream_head = _ref(repo, "refs/remotes/upstream/HEAD", "refs/remotes/upstream/master",
                         "refs/remotes/upstream/main") if upstream else None
    since = last_release(repo, target, upstream)

    revs = ["--right-only", "--cherry-pick", f"{upstream_head}...{target}"] if upstream_head else [target]
    if since:
        revs.append(f"^{since}")
    log = _git(repo, "log", "--reverse", "--no-merges", "--date=short",
               "--format=%h%x09%ad%x09%an%x09%s", *revs)
    upstream_subjects = set(_git(repo, "log", "--format=%s", *upstream).splitlines()) if upstream else set()

    rows = []
    for line in log.splitlines():
        sha, date, author, subject = line.split("\t", 3)
        if subject in upstream_subjects:
            continue
        rows.append((sha, date, author, subject))
    return since, rows


def _repos_for(root: Path, category: str) -> list[str]:
    roster = root / "scripts" / f"version-repos-{'plasma' if category == 'core' else category}.list"
    names = []
    for line in roster.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return sorted(names)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List SonicDE changes since each repository's last release.")
    parser.add_argument("category", nargs="?", choices=["core", "plasma", "frameworks", "system"])
    parser.add_argument("--repo", action="append", default=[], help="repository name (repeatable)")
    parser.add_argument("--root", default=None, help="builder root (default: this script's parent)")
    args = parser.parse_args(argv)
    if bool(args.category) == bool(args.repo):
        parser.error("give either a category or --repo")

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    names = args.repo or _repos_for(root, args.category)
    failures = 0
    print("repo\tlast release\tcommit\tdate\tauthor\tsubject")
    for name in names:
        repo = root / "src" / "sonicde" / name
        try:
            if not (repo / ".git").exists():
                raise ValueError("repository checkout missing")
            since, rows = changes(repo)
        except (OSError, ValueError) as error:
            print(f"[{name}] {error}", file=sys.stderr)
            failures += 1
            continue
        if not rows:
            print(f"{name}\t{since or '-'}\t-\t-\t-\t-")
        for sha, date, author, subject in rows:
            print(f"{name}\t{since or '-'}\t{sha}\t{date}\t{author}\t{subject}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
