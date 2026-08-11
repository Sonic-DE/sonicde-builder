#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Generate repository views and compatibility repo-list from the model."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_model(path: str | Path) -> dict:
    with open(path) as f:
        return json.load(f)


def git_packages(model: dict) -> dict[str, dict]:
    return {k: v for k, v in model["packages"].items() if v.get("git")}


def active_git(model: dict) -> dict[str, dict]:
    """Git-backed packages in the active closure."""
    return git_packages(model)


def managed_sonicde(model: dict) -> dict[str, dict]:
    """All Git-backed packages in the sonicde/ namespace (active closure only)."""
    return {k: v for k, v in active_git(model).items()
            if k.startswith("sonicde/")}


def autopick_repos(model: dict) -> dict[str, dict]:
    """Managed SonicDE repos with autopick.enabled=true and complete config."""
    out = {}
    for k, v in managed_sonicde(model).items():
        cfg = (v.get("git") or {}).get("config", {})
        if str(cfg.get("autopick.enabled", "")).lower() != "true":
            continue
        if not cfg.get("autopick.upstream") or not cfg.get("autopick.master") \
                or not cfg.get("autopick.tracker"):
            continue
        out[k] = v
    return out


def post_autopick_repos(model: dict) -> dict[str, dict]:
    """Managed SonicDE repos selected for local origin refresh."""
    return managed_sonicde(model)


def generate_repo_list(model: dict) -> list[str]:
    """Sorted list of managed SonicDE repository basenames."""
    return sorted(v["name"].split("/")[-1]
                  for v in managed_sonicde(model).values())


def org_repo_names(model: dict) -> list[str]:
    """Sorted GitHub repository names for check-new-repos comparison."""
    return generate_repo_list(model)


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Generate repository views")
    ap.add_argument("-model", default="state/model.json")
    ap.add_argument("--view", choices=["active", "managed", "autopick",
                                       "post-autopick", "org", "repo-list"],
                    default="repo-list")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    model = load_model(args.model)

    views = {
        "active": active_git,
        "managed": managed_sonicde,
        "autopick": autopick_repos,
        "post-autopick": post_autopick_repos,
    }

    if args.view == "repo-list":
        names = generate_repo_list(model)
        if args.out:
            Path(args.out).write_text("\n".join(names) + "\n")
            print(f"repo-list written: {args.out} ({len(names)} repos)")
        else:
            for n in names:
                print(n)
        return 0

    if args.view == "org":
        for n in org_repo_names(model):
            print(n)
        return 0

    pkgs = views[args.view](model)
    for k, v in sorted(pkgs.items()):
        print(k)
    print(f"--- {len(pkgs)} repositories", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
