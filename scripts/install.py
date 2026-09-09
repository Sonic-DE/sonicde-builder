#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Deploy completed CMake packages, in dependency order, after the meta-build."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def install_directories(model: dict) -> list[Path]:
    """Preflight the entire deployment before installing the first package."""
    directories = []
    build_root = Path(model["build_dir"]).resolve()
    expected_prefix = Path(model["install_prefix"])
    for name in model["topo_order"]:
        package = model["packages"][name]
        if package.get("type") == "system" or package.get("buildsystem") == "none":
            continue
        if package.get("buildsystem", "") not in ("", "cmake"):
            raise ValueError(f"Unsupported install build system: {name}")
        directory = (build_root / name).resolve()
        if not directory.is_relative_to(build_root):
            raise ValueError(f"Build path escapes build directory: {name}")
        cache = directory / "CMakeCache.txt"
        if not cache.is_file() or not (directory / "cmake_install.cmake").is_file():
            raise ValueError(f"Missing configured installation for {name}: {directory}")
        prefixes = [line.split("=", 1)[1] for line in cache.read_text().splitlines()
                    if line.startswith("CMAKE_INSTALL_PREFIX:") and "=" in line]
        if len(prefixes) != 1 or Path(prefixes[0]) != expected_prefix:
            raise ValueError(f"Configured install prefix differs from model for {name}")
        directories.append(directory)
    return directories


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-model", default="state/model.json")
    args = parser.parse_args(argv)
    try:
        model = json.loads(Path(args.model).read_text())
        directories = install_directories(model)
        for directory in directories:
            print(f"Installing {directory} -> {model['install_prefix']}", flush=True)
            subprocess.run(["cmake", "--install", str(directory)], check=True)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"System installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
