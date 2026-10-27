#!/usr/bin/env python3
"""Version bump scripts for SonicDE repos.

Reads the explicit Core, Frameworks, and System repository lists, updates
existing release version fields in CMakeLists.txt and commits any change.
Tag creation is a separate --tag-only operation. Unlike KDE's release scripts,
no second "bump to next dev version" commit is created -- we make patch
releases against these tagged versions (e.g. 6.7.4.1).

Usage:
    version_bump.py core <version> [--dry-run] [--list]
    version_bump.py frameworks <version> [--dry-run] [--list]
    version_bump.py system <version> [--dry-run] [--list]
    version_bump.py project-dep <version> [--dry-run] [--no-push]
    version_bump.py core <version> --tag-only [--dry-run] [--no-push]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------
#
# Repository membership comes only from version-repos-<category>.list.
# These replacement functions edit existing release fields, not select repos.

PLASMA_VAR = "PROJECT_VERSION"
FRAMEWORKS_VAR = "KF_VERSION"
SYSTEM_VARS = (
    "RELEASE_SERVICE_VERSION_MAJOR",
    "RELEASE_SERVICE_VERSION_MINOR",
    "RELEASE_SERVICE_VERSION_MICRO",
)

PROJECT_DEP_SET = re.compile(
    r'(^[ \t]*(?i:set)\s*\(\s*PROJECT_DEP_VERSION\s+)("?)(\d+(?:\.\d+)+)\2(?=\s|\))',
    re.MULTILINE,
)


def _replace_quoted_set(content: str, var: str, value: str) -> str:
    """Replace ``set(VAR "old")`` (or ``set (VAR "old")``) with the new value.

    Handles optional whitespace between ``set`` and ``(`` and after ``(``,
    and preserves any trailing comment or closing ``)``.
    """
    pattern = rf'set\s*\(\s*{re.escape(var)}\s+"[^"]*"'
    replacement = f'set({var} "{value}"'
    return re.sub(pattern, replacement, content)


def _replace_ecm_version(content: str, version: str) -> str:
    """Replace the version in ``find_package(ECM X.Y.Z ... NO_MODULE)``.

    Handles optional whitespace, optional REQUIRED, and ``set (`` spacing.
    Only matches a literal version (not a ``${...}`` variable).
    """
    pattern = r'(find_package\s*\(\s*ECM\s+)\d+\.\d+\.\d+(\s+)'
    return re.sub(pattern, rf'\g<1>{version}\g<2>', content)


def _replace_plasma_version(content: str, version: str) -> str:
    content = _replace_quoted_set(content, PLASMA_VAR, version)
    # sonic-breeze-sddm declares its release version directly in project().
    # Variable-based project versions are already handled by set() above.
    pattern = r'(\bproject\s*\([^)]*?\bVERSION\s+)("?)(\d+\.\d+\.\d+(?:\.\d+)?)\2(?=\s|\))'
    return re.sub(pattern, lambda match: f'{match[1]}{match[2]}{version}{match[2]}', content, count=1)


def _replace_frameworks_version(content: str, version: str) -> str:
    """Update all three Frameworks version locations.

    1. ``set(KF_VERSION "old")``           -> ``set(KF_VERSION "new")``
    2. ``set(KF_DEP_VERSION "old")``       -> ``set(KF_DEP_VERSION "new")``
    3. ``find_package(ECM old ... NO_MODULE)`` -> ``find_package(ECM new ... NO_MODULE)``
    """
    content = _replace_quoted_set(content, FRAMEWORKS_VAR, version)
    content = _replace_quoted_set(content, "KF_DEP_VERSION", version)
    content = _replace_ecm_version(content, version)
    return content


def _replace_system_version(content: str, version: str) -> str:
    """Replace the three RELEASE_SERVICE_VERSION_* variables."""
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"system version must be X.Y.Z, got {version}")
    for var, part in zip(SYSTEM_VARS, parts):
        content = _replace_quoted_set(content, var, part)
    return content


CATEGORIES: dict[str, dict] = {
    "core": {
        "replace": _replace_plasma_version,
    },
    # Keep the internal Plasma category compatible with tracker tooling.
    "plasma": {
        "replace": _replace_plasma_version,
    },
    "frameworks": {
        "replace": _replace_frameworks_version,
    },
    "system": {
        "replace": _replace_system_version,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_version(version: str) -> bool:
    """Return True if *version* looks like ``X.Y.Z``."""
    return bool(re.match(r"^\d+\.\d+\.\d+$", version))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _prepare_origin_master(repo: Path) -> None:
    """Start on attached master at origin/master, without discarding local work."""
    def run(*args: str) -> str:
        result = _git(repo, *args)
        if result.returncode:
            raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    if run("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("working tree is not clean")
    git_dir = Path(run("rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    if any((git_dir / state).exists() for state in (
            "rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer")):
        raise ValueError("a rebase, merge, or cherry-pick is still in progress")

    run("fetch", "--quiet", "--no-tags", "origin",
        "+refs/heads/master:refs/remotes/origin/master")
    remote = run("rev-parse", "--verify", "refs/remotes/origin/master^{commit}")
    local = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/master")
    if local.returncode == 0:
        ancestor = _git(repo, "merge-base", "--is-ancestor", "refs/heads/master", "refs/remotes/origin/master")
        if ancestor.returncode:
            raise ValueError("local master has unpushed/diverged commits or could not be checked; refusing to overwrite it")
    elif local.returncode != 1:
        raise ValueError(f"cannot inspect local master: {local.stderr.strip()}")

    current_branch = _git(repo, "symbolic-ref", "--quiet", "HEAD")
    if current_branch.returncode == 1:
        detached = _git(repo, "merge-base", "--is-ancestor", "HEAD", "refs/remotes/origin/master")
        if detached.returncode:
            raise ValueError("detached HEAD has commits outside origin/master; refusing to abandon them")
    elif current_branch.returncode != 0:
        raise ValueError(f"cannot inspect HEAD: {current_branch.stderr.strip()}")

    if local.returncode == 0:
        run("checkout", "--quiet", "master")
        run("merge", "--quiet", "--ff-only", "refs/remotes/origin/master")
    else:
        run("checkout", "--quiet", "-b", "master", "--track", "origin/master")
    if run("symbolic-ref", "--quiet", "HEAD") != "refs/heads/master" or run("rev-parse", "HEAD") != remote:
        raise ValueError("checkout is not attached to master at the fetched origin/master commit")
    print(f"[{repo.name}] using master at origin/master ({remote[:12]})")


RELEASE_NOTES = "RELEASE-NOTES.md"


def _publish_release_notes(repo: Path, version: str, dry_run: bool, push: bool) -> str | None:
    """Commit and push pending RELEASE-NOTES.md changes on master before tagging.

    Only the release notes are committed, on master at origin/master, so the
    tag lands on a commit that contains them. Returns the pending notes text
    for a dry run, otherwise None.
    """
    pending = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", RELEASE_NOTES)
    if pending.returncode:
        raise ValueError(f"cannot inspect {RELEASE_NOTES}: {pending.stderr.strip()}")
    if not pending.stdout.strip():
        return None
    if dry_run:
        print(f"[{repo.name}] would commit and push {RELEASE_NOTES} to master before tagging")
        return (repo / RELEASE_NOTES).read_text(encoding="utf-8")
    if not push:
        raise ValueError(f"{RELEASE_NOTES} has unpublished changes; the tag would miss them without a push")

    def run(*args: str) -> str:
        result = _git(repo, *args)
        if result.returncode:
            raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    run("fetch", "--quiet", "--no-tags", "origin", "+refs/heads/master:refs/remotes/origin/master")
    if _git(repo, "symbolic-ref", "--quiet", "HEAD").stdout.strip() != "refs/heads/master" \
            or run("rev-parse", "HEAD") != run("rev-parse", "refs/remotes/origin/master"):
        raise ValueError(f"{RELEASE_NOTES} can only be published from master at origin/master")
    run("add", "--", RELEASE_NOTES)
    run("commit", "--quiet", "-m", f"Release notes for {version}", "--", RELEASE_NOTES)
    run("push", "--quiet", "--no-follow-tags", "origin", "HEAD:refs/heads/master")
    print(f"[{repo.name}] committed and pushed {RELEASE_NOTES} for {version}")
    return None


def _check_release_notes(notes: str, version: str) -> None:
    """Refuse to tag a release whose notes have no section for it."""
    if not re.search(rf"^## {re.escape(version)}(?:[ \t]|$)", notes, re.MULTILINE):
        raise ValueError(f"{RELEASE_NOTES} has no section for {version}; generate the release notes first")


def _find_repos(root: Path, category: str) -> list[str]:
    """Read the maintained list; never infer membership from CMake or tags."""
    roster_category = "plasma" if category == "core" else category
    roster = root / "scripts" / f"version-repos-{roster_category}.list"
    repos: list[str] = []
    for number, line in enumerate(roster.read_text(encoding="utf-8").splitlines(), 1):
        name = line.split("#", 1)[0].strip()
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError(f"{roster}:{number}: invalid repository name {name!r}")
        if name in repos:
            raise ValueError(f"{roster}:{number}: duplicate repository {name!r}")
        repos.append(name)
    return sorted(repos)


def _get_current_version(cmake: Path, category: str) -> str | None:
    """Extract the current version from a CMakeLists.txt."""
    return version_from_cmake(cmake.read_text(encoding="utf-8"), category)


def version_from_cmake(content: str, category: str) -> str | None:
    """Extract the release version from CMakeLists.txt content."""
    if category == "system":
        majors = re.findall(
            rf'set\s*\(\s*{re.escape(SYSTEM_VARS[0])}\s+"([^"]*)"', content
        )
        minors = re.findall(
            rf'set\s*\(\s*{re.escape(SYSTEM_VARS[1])}\s+"([^"]*)"', content
        )
        micros = re.findall(
            rf'set\s*\(\s*{re.escape(SYSTEM_VARS[2])}\s+"([^"]*)"', content
        )
        if majors and minors and micros:
            return f"{majors[0]}.{minors[0]}.{micros[0]}"
        return None

    var = PLASMA_VAR if category in ("core", "plasma") else FRAMEWORKS_VAR
    matches = re.findall(rf'set\s*\(\s*{re.escape(var)}\s+"([^"]*)"', content)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def _project_dep_files(repo: Path) -> dict[str, str]:
    """Read dependency declarations from the current checkout's tracked files."""
    result = _git(repo, "ls-files", "-z", "--", "CMakeLists.txt", "**/CMakeLists.txt", "*.cmake", "*.cmake.in")
    if result.returncode:
        raise ValueError(f"[{repo.name}] cannot list tracked CMake files: {result.stderr.strip()}")
    files = {}
    for name in result.stdout.split("\0"):
        path = repo / name
        if not name or path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes().decode("utf-8")
        if PROJECT_DEP_SET.search(content):
            files[name] = content
    return files


def _source_repositories(root: Path) -> list[Path]:
    source_root = root / "src" / "sonicde"
    if not source_root.is_dir():
        raise ValueError(f"SonicDE source directory missing: {source_root}")
    return [repo for repo in sorted(source_root.iterdir())
            if repo.is_dir() and (repo / ".git").exists()]


def _project_dep_sources(root: Path) -> list[tuple[Path, dict[str, str]]]:
    """Discover declarations without changing checkouts (for previews and lists)."""
    sources = []
    for repo in _source_repositories(root):
        files = _project_dep_files(repo)
        if files:
            sources.append((repo, files))
    return sources


def bump_project_dependencies(root: Path, version: str, dry_run: bool, push: bool) -> int:
    """Commit dependency-only bumps everywhere; never create release tags."""
    processed = failures = 0
    message = f"Project Dependency {version}"
    sources = _project_dep_sources(root) if dry_run else ((repo, {}) for repo in _source_repositories(root))
    for repo, files in sources:
        if not dry_run:
            try:
                _prepare_origin_master(repo)
                # Discover from master, not from an old checkout or sync branch.
                files = _project_dep_files(repo)
            except (OSError, ValueError) as error:
                print(f"[{repo.name}] cannot prepare origin/master: {error}", file=sys.stderr)
                failures += 1
                continue
        if not files:
            continue
        changed = {}
        for name, content in files.items():
            replacement = PROJECT_DEP_SET.sub(
                lambda match: f"{match[1]}{match[2]}{version}{match[2]}", content)
            if replacement != content:
                changed[name] = replacement
        if not changed:
            print(f"[{repo.name}] PROJECT_DEP_VERSION already at {version}")
            continue
        if dry_run:
            print(f"[{repo.name}] would update {', '.join(changed)}; commit: {message}")
            continue

        for name, content in changed.items():
            (repo / name).write_bytes(content.encode("utf-8"))
        staged = _git(repo, "add", "--", *changed)
        committed = _git(repo, "commit", "-m", message, "--", *changed) if staged.returncode == 0 else staged
        if committed.returncode:
            print(f"[{repo.name}] dependency commit failed: {committed.stderr.strip()}", file=sys.stderr)
            failures += 1
            continue
        processed += 1
        print(f"[{repo.name}] committed: {message}")
        if push:
            result = _git(repo, "push", "--no-follow-tags", "origin", "HEAD:refs/heads/master")
            if result.returncode:
                print(f"[{repo.name}] push failed: {result.stderr.strip()}", file=sys.stderr)
                failures += 1
    print(f"\n[project-dep] {'dry run' if dry_run else f'committed {processed} repo(s)'}; failures: {failures}")
    return 1 if failures else 0


def bump_category(
    root: Path,
    category: str,
    version: str,
    dry_run: bool = False,
    push: bool = True,
) -> int:
    """Bump all repos in *category* to *version*.

    Returns the number of repos processed.
    """
    cat = CATEGORIES[category]
    repos = _find_repos(root, category)
    if not repos:
        print(f"[{category}] no matching repos found", file=sys.stderr)
        return 0

    processed = failures = 0
    for repo_name in repos:
        repo_dir = root / "src" / "sonicde" / repo_name
        cmake = repo_dir / "CMakeLists.txt"

        # Do not accidentally run git in the enclosing builder repository.
        if not (repo_dir / ".git").exists():
            print(f"[{repo_name}] repository checkout missing, skipping",
                  file=sys.stderr)
            failures += 1
            continue

        if not dry_run:
            try:
                _prepare_origin_master(repo_dir)
            except (OSError, ValueError) as error:
                print(f"[{repo_name}] cannot prepare origin/master: {error}", file=sys.stderr)
                failures += 1
                continue

        current = _get_current_version(cmake, category) if cmake.exists() else None
        content = cmake.read_text(encoding="utf-8") if cmake.exists() else ""
        new_content = cat["replace"](content, version)
        if category == "frameworks" and repo_name == "sonic-frameworks-cmake-modules":
            new_content = _replace_quoted_set(new_content, "VERSION", version)
        cmake_changed = new_content != content

        if dry_run:
            if cmake_changed:
                print(f"[{repo_name}] would bump {current or 'CMake version'} -> {version} and commit (no tag)")
            else:
                print(f"[{repo_name}] no CMake version change; no commit or tag")
            continue

        # Version updates never inspect, create, or push release tags.
        if cmake_changed:
            cmake.write_text(new_content, encoding="utf-8")
            add_result = _git(repo_dir, "add", "CMakeLists.txt")
            if add_result.returncode != 0:
                print(f"[{repo_name}] staging failed: {add_result.stderr.strip()}", file=sys.stderr)
                failures += 1
                continue
            commit_result = _git(repo_dir, "commit", "-m", f"Release {version}")
            if commit_result.returncode != 0:
                print(f"[{repo_name}] commit failed: {commit_result.stderr}", file=sys.stderr)
                failures += 1
                continue
            print(f"[{repo_name}] bumped {current or 'CMake version'} -> {version}")
        else:
            print(f"[{repo_name}] no CMake version change; no commit or tag")

        if push and cmake_changed:
            push_result = _git(repo_dir, "push", "--no-follow-tags", "origin", "HEAD:refs/heads/master")
            if push_result.returncode != 0:
                print(
                    f"[{repo_name}] push failed: "
                    f"{push_result.stderr.strip()}",
                    file=sys.stderr,
                )
                failures += 1
            else:
                print(f"[{repo_name}] pushed master to origin (no tags)")

        processed += 1

    if failures:
        raise RuntimeError(f"[{category}] {failures} repository operation(s) failed; processed {processed}")
    return processed


def tag_category(root: Path, category: str, version: str,
                 dry_run: bool = False, push: bool = True) -> int:
    """Tag the accepted origin/master commit; never edit CMake files.

    Pending RELEASE-NOTES.md changes are committed and pushed to master first,
    so the tag includes them. A repository that has release notes must have a
    section for *version*.
    """
    processed = failures = 0
    for name in _find_repos(root, category):
        repo = root / "src" / "sonicde" / name
        try:
            if not (repo / ".git").exists():
                raise ValueError("repository checkout missing")
            pending_notes = _publish_release_notes(repo, version, dry_run, push)
            if not dry_run:
                _prepare_origin_master(repo)

            target = _git(repo, "rev-parse", "--verify", "refs/remotes/origin/master^{commit}")
            if target.returncode:
                raise ValueError(f"cannot resolve origin/master: {target.stderr.strip()}")
            sha = target.stdout.strip()
            # Read the accepted source, not the branch that happens to be checked out.
            source = _git(repo, "ls-tree", "--name-only", sha, "--", "CMakeLists.txt")
            if source.returncode:
                raise ValueError(f"cannot inspect origin/master: {source.stderr.strip()}")
            if source.stdout.strip():
                content = _git(repo, "show", f"{sha}:CMakeLists.txt")
                if content.returncode:
                    raise ValueError(f"cannot read accepted CMake file: {content.stderr.strip()}")
                updated = CATEGORIES[category]["replace"](content.stdout, version)
                if category == "frameworks" and name == "sonic-frameworks-cmake-modules":
                    updated = _replace_quoted_set(updated, "VERSION", version)
                if updated != content.stdout:
                    raise ValueError(f"origin/master CMake version is not {version}; run the regular version-bump command first")

            if pending_notes is not None:
                _check_release_notes(pending_notes, version)
            else:
                notes = _git(repo, "show", f"{sha}:{RELEASE_NOTES}")
                if notes.returncode == 0:
                    _check_release_notes(notes.stdout, version)

            tag_ref = f"refs/tags/{version}"
            existing = _git(repo, "show-ref", "--verify", "--quiet", tag_ref)
            if existing.returncode == 0:
                peeled = _git(repo, "rev-parse", "--verify", f"{tag_ref}^{{commit}}")
                if peeled.returncode or peeled.stdout.strip() != sha:
                    raise ValueError(f"tag {version} already points elsewhere; refusing to move it")
            elif existing.returncode != 1:
                raise ValueError(f"cannot inspect tag {version}: {existing.stderr.strip()}")

            if dry_run:
                action = "reuse" if existing.returncode == 0 else "create"
                print(f"[{name}] would {action} tag {version} at origin/master {sha[:12]} (no CMake edits)")
                continue
            if existing.returncode == 1:
                created = _git(repo, "tag", version, sha)
                if created.returncode:
                    raise ValueError(f"cannot create tag: {created.stderr.strip()}")
            if push:
                result = _git(repo, "push", "--no-follow-tags", "origin", f"{tag_ref}:{tag_ref}")
                if result.returncode:
                    raise ValueError(f"tag push failed: {result.stderr.strip()}")
            print(f"[{name}] tagged {version} at origin/master {sha[:12]}")
            processed += 1
        except (OSError, ValueError) as error:
            print(f"[{name}] {error}", file=sys.stderr)
            failures += 1
    if failures:
        raise RuntimeError(f"[{category} tags] {failures} repository operation(s) failed; processed {processed}")
    return processed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bump version for SonicDE repos by category."
    )
    parser.add_argument(
        "category",
        choices=["core", "plasma", "frameworks", "system", "project-dep"],
        help="Version category",
    )
    parser.add_argument("version", nargs="?", help="New version (X.Y.Z)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without modifying files",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Do not push this operation's commits or tags to origin (push is the default)",
    )
    parser.add_argument("--tag-only", action="store_true",
                        help="only tag accepted origin/master, after publishing pending RELEASE-NOTES.md; "
                             "do not update CMake")
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the maintained repository list and exit",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Project root (default: auto-detect from script location)",
    )

    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent

    if args.category == "project-dep":
        if args.tag_only:
            parser.error("project-dep updates do not create release tags")
        if args.version and not _validate_version(args.version):
            parser.error(f"version must be in X.Y.Z format, got {args.version!r}")
        if not args.version and not args.list:
            parser.error("version is required unless --list is given")
        try:
            if args.list:
                for repo, _ in _project_dep_sources(root):
                    print(repo.name)
                return 0
            return bump_project_dependencies(root, args.version, args.dry_run, not args.no_push)
        except (OSError, UnicodeError, ValueError) as error:
            parser.error(str(error))

    try:
        _find_repos(root, args.category)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    if args.list:
        repos = _find_repos(root, args.category)
        for repo in repos:
            print(repo)
        return 0

    if not args.version:
        parser.error("version is required unless --list is given")

    if not _validate_version(args.version):
        parser.error(f"version must be in X.Y.Z format, got {args.version!r}")

    operation = tag_category if args.tag_only else bump_category
    try:
        n = operation(root, args.category, args.version,
                      dry_run=args.dry_run, push=not args.no_push)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"\n[{args.category}] processed {n} repo(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
