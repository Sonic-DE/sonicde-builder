#!/usr/bin/env python3
"""Version bump scripts for SonicDE repos.

Identifies repos by their CMakeLists.txt version variable pattern, bumps the
version, commits the change, and creates a tag.  Unlike KDE's release scripts,
no second "bump to next dev version" commit is created -- we make patch
releases against these tagged versions (e.g. 6.7.4.1).

Usage:
    version_bump.py plasma <version> [--dry-run] [--list]
    version_bump.py frameworks <version> [--dry-run] [--list]
    version_bump.py system <version> [--dry-run] [--list]
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
# Each category is identified by a regex that matches the version-variable
# declaration in CMakeLists.txt.  The *replace* function takes the file content
# and the new version string and returns the updated content.

PLASMA_VAR = "PROJECT_VERSION"
FRAMEWORKS_VAR = "KF_VERSION"
SYSTEM_VARS = (
    "RELEASE_SERVICE_VERSION_MAJOR",
    "RELEASE_SERVICE_VERSION_MINOR",
    "RELEASE_SERVICE_VERSION_MICRO",
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
    "plasma": {
        "match": re.compile(rf'set\s*\(\s*{re.escape(PLASMA_VAR)}\s+"6\.'),
        "replace": lambda content, version: _replace_quoted_set(
            content, PLASMA_VAR, version
        ),
        "current_prefix": "6.",
    },
    "frameworks": {
        "match": re.compile(rf'set\s*\(\s*{re.escape(FRAMEWORKS_VAR)}\s+"\d'),
        "replace": _replace_frameworks_version,
        "current_prefix": None,
    },
    "system": {
        "match": re.compile(
            rf'set\s*\(\s*{re.escape(SYSTEM_VARS[0])}\s+"'
        ),
        "replace": _replace_system_version,
        "current_prefix": None,
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


def _find_repos(root: Path, category: str) -> list[str]:
    """Return sorted list of repo names that belong to *category*."""
    cat = CATEGORIES[category]
    repos: list[str] = []
    for cmake in sorted(root.glob("src/sonicde/*/CMakeLists.txt")):
        try:
            content = cmake.read_text(encoding="utf-8")
        except OSError:
            continue
        if cat["match"].search(content):
            repos.append(cmake.parent.name)
    return repos


def _get_current_version(cmake: Path, category: str) -> str | None:
    """Extract the current version from a CMakeLists.txt."""
    content = cmake.read_text(encoding="utf-8")
    cat = CATEGORIES[category]

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

    var = PLASMA_VAR if category == "plasma" else FRAMEWORKS_VAR
    matches = re.findall(rf'set\s*\(\s*{re.escape(var)}\s+"([^"]*)"', content)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


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

    processed = 0
    for repo_name in repos:
        repo_dir = root / "src" / "sonicde" / repo_name
        cmake = repo_dir / "CMakeLists.txt"

        if not cmake.exists():
            print(f"[{repo_name}] CMakeLists.txt missing, skipping",
                  file=sys.stderr)
            continue

        current = _get_current_version(cmake, category)
        if current == version:
            print(f"[{repo_name}] already at {version}, skipping")
            continue

        # Update the version
        content = cmake.read_text(encoding="utf-8")
        new_content = cat["replace"](content, version)
        if new_content == content:
            print(
                f"[{repo_name}] version variable not found in CMakeLists.txt, "
                f"skipping",
                file=sys.stderr,
            )
            continue

        if dry_run:
            print(f"[{repo_name}] would bump {current} -> {version}")
            continue

        # Check for dirty working tree
        status = _git(repo_dir, "status", "--porcelain", "--untracked-files=no")
        if status.stdout.strip():
            print(
                f"[{repo_name}] working tree not clean, skipping",
                file=sys.stderr,
            )
            continue

        # Write the updated CMakeLists.txt
        cmake.write_text(new_content, encoding="utf-8")

        # Commit
        _git(repo_dir, "add", "CMakeLists.txt")
        commit_msg = f"Release {version}"
        commit_result = _git(repo_dir, "commit", "-m", commit_msg)
        if commit_result.returncode != 0:
            print(
                f"[{repo_name}] commit failed: {commit_result.stderr}",
                file=sys.stderr,
            )
            continue

        # Create tag (don't overwrite if it exists)
        tag = f"{version}"
        tag_result = _git(repo_dir, "tag", tag)
        if tag_result.returncode != 0:
            print(
                f"[{repo_name}] tag {tag} already exists or failed: "
                f"{tag_result.stderr.strip()}",
                file=sys.stderr,
            )
            # The commit is still valid even if the tag exists
        else:
            print(f"[{repo_name}] bumped {current} -> {version}, tagged {tag}")

        # Push commit and tag to origin
        if push:
            push_result = _git(repo_dir, "push", "origin", "HEAD", tag)
            if push_result.returncode != 0:
                print(
                    f"[{repo_name}] push failed: "
                    f"{push_result.stderr.strip()}",
                    file=sys.stderr,
                )
            else:
                print(f"[{repo_name}] pushed {tag} to origin")

        processed += 1

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
        choices=["plasma", "frameworks", "system"],
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
        help="Do not push commits and tags to origin (push is the default)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List matching repos and exit",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Project root (default: auto-detect from script location)",
    )

    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent

    if args.list:
        repos = _find_repos(root, args.category)
        for repo in repos:
            print(repo)
        return 0

    if not args.version:
        parser.error("version is required unless --list is given")

    if not _validate_version(args.version):
        parser.error(f"version must be in X.Y.Z format, got {args.version!r}")

    n = bump_category(
        root, args.category, args.version,
        dry_run=args.dry_run, push=not args.no_push,
    )
    print(f"\n[{args.category}] processed {n} repo(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
