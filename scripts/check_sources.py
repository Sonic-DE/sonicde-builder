#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the entire active source closure before launching CMake jobs."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def source_problems(model: dict) -> list[str]:
    problems = []
    root = Path(model["source_root"])
    for name in model["topo_order"]:
        package = model["packages"][name]
        if package.get("type") == "system":
            continue
        source = root / name
        if not source.is_dir():
            problems.append(f"[{name}] missing source directory: {source}")
        elif package.get("buildsystem", "") in ("", "cmake") and not (source / "CMakeLists.txt").is_file():
            problems.append(f"[{name}] missing CMakeLists.txt: {source / 'CMakeLists.txt'}")
        elif (source / ".gitmodules").is_file() and (source / ".git").exists():
            status = subprocess.run(["git", "submodule", "status", "--recursive"], cwd=source,
                                    capture_output=True, text=True)
            if status.returncode != 0:
                problems.append(f"[{name}] cannot inspect submodules: {status.stderr.strip()}")
            else:
                missing = [line[1:].split(" ", 1)[1].split(" (", 1)[0]
                           for line in status.stdout.splitlines() if line.startswith("-")]
                if missing:
                    problems.append(f"[{name}] uninitialized submodules: {', '.join(missing)}")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-model", default="state/model.json")
    args = parser.parse_args(argv)
    try:
        problems = source_problems(json.loads(Path(args.model).read_text()))
    except (OSError, ValueError, KeyError) as error:
        print(f"Source preflight failed: {error}", file=sys.stderr)
        return 1
    if problems:
        print("Source preflight failed:\n" + "\n".join(problems), file=sys.stderr)
        print("Run ./fetch-all and check for fetch failures.\n"
              "Empty initial checkouts can be resumed safely. If a repository already has a commit,\n"
              "inspect its checked-out ref and local deletions; the builder will not reset it.\n"
              "No configuration, compilation, sudo authentication, or system installation was started.",
              file=sys.stderr)
        return 1
    print("Source preflight passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
