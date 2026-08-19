#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Mirror upstream release tags onto patch-equivalent SonicDE commits."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from repositories import autopick_repos


def run_git(repo: Path, args: list[str], check: bool = True,
            input_text: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(repo), input=input_text,
        capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}:\n{result.stderr}")
    return result


def split_remote_ref(value: str) -> tuple[str, str]:
    """Split a configured remote-tracking ref such as upstream/master."""
    remote, separator, branch = value.partition("/")
    if not separator or not remote or not branch:
        raise ValueError(f"invalid remote-tracking ref: {value}")
    return remote, branch


def fetch_remote(repo: Path, remote_ref: str) -> tuple[str, str]:
    """Force-refresh one branch and its namespaced remote tags."""
    remote, branch = split_remote_ref(remote_ref)
    run_git(repo, [
        "fetch", "--force", "--prune", remote,
        f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}",
        f"+refs/tags/*:refs/tags/{remote}/*",
    ])
    return remote, branch


def latest_upstream_tag(repo: Path, upstream_remote: str,
                        upstream_ref: str) -> tuple[str, str] | None:
    """Return the newest upstream tag reachable from the configured branch."""
    prefix = f"refs/tags/{upstream_remote}"
    result = run_git(repo, [
        "for-each-ref", "--sort=-version:refname",
        "--sort=-creatordate",
        "--format=%(refname)", prefix,
    ])
    for full_ref in result.stdout.splitlines():
        tag_name = full_ref.removeprefix(prefix + "/")
        if not tag_name or tag_name == full_ref:
            continue
        commit = run_git(
            repo, ["rev-parse", f"{full_ref}^{{commit}}"]
        ).stdout.strip()
        reachable = run_git(
            repo, ["merge-base", "--is-ancestor", commit, upstream_ref],
            check=False)
        if reachable.returncode == 0:
            return tag_name, commit
        if reachable.returncode != 1:
            raise RuntimeError(
                f"cannot test whether {full_ref} is on {upstream_ref}:\n"
                f"{reachable.stderr}")
    return None


def patch_id(repo: Path, commit: str) -> str | None:
    """Calculate the stable patch ID for one non-merge commit."""
    shown = run_git(repo, [
        "show", "--pretty=format:", "--no-ext-diff", "--binary", commit,
    ])
    result = run_git(
        repo, ["patch-id", "--stable"], input_text=shown.stdout)
    fields = result.stdout.split()
    return fields[0] if fields else None


def context_free_patch_id(repo: Path, commit: str) -> str | None:
    """Identify a commit's changes without surrounding-context differences."""
    shown = run_git(repo, [
        "show", "--pretty=format:", "--no-ext-diff", "--binary",
        "--unified=0", commit,
    ])
    result = run_git(
        repo, ["patch-id", "--stable"], input_text=shown.stdout)
    fields = result.stdout.split()
    return fields[0] if fields else None


def equivalent_origin_commit(repo: Path, upstream_commit: str,
                             upstream_ref: str,
                             origin_ref: str) -> str | None:
    """Find the origin commit produced by rebasing an upstream commit."""
    contained = run_git(
        repo, ["merge-base", "--is-ancestor", upstream_commit, origin_ref],
        check=False)
    if contained.returncode == 0:
        return upstream_commit
    if contained.returncode != 1:
        raise RuntimeError(
            f"cannot inspect {upstream_commit} against {origin_ref}:\n"
            f"{contained.stderr}")

    # Autopick rebases an ordered upstream range onto origin/master. Nearby
    # downstream edits can change a normal patch ID by changing hunk context.
    # range-diff supplies the sequence mapping, but it is heuristic, so its
    # candidate is accepted only if a context-free patch ID proves that the
    # file changes and added/deleted lines are identical.
    common_base = run_git(
        repo, ["merge-base", upstream_ref, origin_ref]
    ).stdout.strip()
    range_diff = run_git(repo, [
        "range-diff", "--no-patch", "--no-color", "--abbrev=40",
        f"{common_base}..{upstream_ref}",
        f"{common_base}..{origin_ref}",
    ])
    mapping = re.compile(
        r"^\s*\d+:\s+([0-9a-f]{40})\s+[=!]\s+"
        r"\d+:\s+([0-9a-f]{40})(?:\s|$)")
    mapped_candidate = None
    for line in range_diff.stdout.splitlines():
        match = mapping.match(line)
        if match and match.group(1) == upstream_commit:
            mapped_candidate = match.group(2)
            break

    upstream_change = context_free_patch_id(repo, upstream_commit)
    if not upstream_change:
        return None
    if mapped_candidate and context_free_patch_id(
            repo, mapped_candidate) == upstream_change:
        return mapped_candidate

    commits = run_git(
        repo, ["rev-list", "--first-parent", f"{common_base}..{origin_ref}"]
    ).stdout.splitlines()
    matches = []
    for candidate in commits:
        if context_free_patch_id(repo, candidate) == upstream_change:
            matches.append(candidate)

    # Never guess between repeated identical changes. The range mapping may
    # disambiguate them, but only after the exact change check above.
    if len(matches) == 1:
        return matches[0]
    if mapped_candidate in matches:
        return mapped_candidate
    return None


def origin_tag_name(upstream_tag: str) -> str:
    """Convert an upstream v-prefixed release tag to the SonicDE tag name."""
    return upstream_tag[1:] if upstream_tag.startswith("v") else upstream_tag


def sync_repo(pkg_name: str, pkg: dict, repo: Path,
              dry_run: bool = False) -> tuple[str, str | None] | None:
    """Synchronize the latest reachable upstream tag for one repository."""
    config = pkg["git"].get("config", {})
    upstream_ref = config["autopick.upstream"]
    origin_ref = config["autopick.master"]
    upstream_remote, _ = fetch_remote(repo, upstream_ref)
    origin_remote, _ = fetch_remote(repo, origin_ref)

    latest = latest_upstream_tag(repo, upstream_remote, upstream_ref)
    if latest is None:
        return None
    upstream_tag, upstream_commit = latest
    tag_name = origin_tag_name(upstream_tag)
    current = run_git(
        repo, ["ls-remote", "--tags", origin_remote,
               f"refs/tags/{tag_name}^{{}}"], check=False)
    current_commit = current.stdout.split()[0] if current.stdout.split() else ""
    if not current_commit:
        direct = run_git(
            repo, ["ls-remote", "--tags", origin_remote,
                   f"refs/tags/{tag_name}"], check=False)
        current_commit = direct.stdout.split()[0] if direct.stdout.split() else ""

    # Published tags are immutable. If origin already has this tag, preserve
    # it regardless of which commit it references and do so silently.
    if current_commit:
        return None

    target = equivalent_origin_commit(
        repo, upstream_commit, upstream_ref, origin_ref)
    if target is None:
        raise RuntimeError(
            f"no patch-equivalent commit on {origin_ref} for "
            f"{upstream_remote}/{upstream_tag} ({upstream_commit})")
    if dry_run:
        return f"would create tag {tag_name} at {target}", None

    # Do not force: if another process creates the tag after ls-remote, Git
    # must reject this push rather than replacing the newly published tag.
    run_git(repo, [
        "push", origin_remote,
        f"{target}:refs/tags/{tag_name}",
    ])
    return f"set tag {tag_name} at {target}", tag_name


def initialize_tracker(path: Path | None) -> None:
    """Create or clear one tracker file at the start of a run."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


def append_tracker(path: Path | None, line: str) -> None:
    """Persist one result immediately so interrupted runs retain progress."""
    if path is None:
        return
    with path.open("a") as tracker:
        tracker.write(f"{line}\n")


def sync_all(model: dict, dry_run: bool = False,
             only: set[str] | None = None,
             failed_file: Path | None = None,
             success_file: Path | None = None) -> int:
    """Synchronize tags for active SonicDE autopick repositories."""
    errors = 0
    initialize_tracker(failed_file)
    initialize_tracker(success_file)
    source_root = Path(model["source_root"])
    for pkg_name, pkg in sorted(autopick_repos(model).items()):
        basename = pkg_name.rsplit("/", 1)[-1]
        if only and pkg_name not in only and basename not in only:
            continue
        repo = source_root / pkg_name
        if not (repo / ".git").exists():
            print(f"[{pkg_name}] FAILED: checkout missing: {repo}",
                  file=sys.stderr)
            append_tracker(failed_file, basename)
            errors += 1
            continue
        try:
            result = sync_repo(pkg_name, pkg, repo, dry_run=dry_run)
            if result is not None:
                message, synced_tag = result
                if synced_tag is not None:
                    append_tracker(
                        success_file, f"{basename}: {synced_tag}")
                print(f"[{pkg_name}] {message}")
        except Exception as error:
            print(f"[{pkg_name}] FAILED: {error}", file=sys.stderr)
            append_tracker(failed_file, basename)
            errors += 1
    return errors


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Mirror latest upstream tags to SonicDE commits")
    parser.add_argument("-model", default="state/model.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args(argv)

    with open(args.model) as model_file:
        model = json.load(model_file)
    state_dir = Path(args.model).resolve().parent
    errors = sync_all(
        model, dry_run=args.dry_run, only=set(args.only) or None,
        failed_file=state_dir / "failed-sync-tag-repos.txt",
        success_file=state_dir / "success-sync-tag-repos.txt")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
