#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a build, check that every package found the CMake packages SonicDE builds in the stage.

A package that found one elsewhere (a distribution's KDE Frameworks, say) is missing a dependency
in its manifest: it was configured before the SonicDE package that provides it was staged.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONFIG_SUFFIXES = ("Config.cmake", "-config.cmake")


def _config_package(file_name: str) -> str | None:
    for suffix in CONFIG_SUFFIXES:
        if file_name.endswith(suffix) and not file_name.endswith(("Version.cmake", "-version.cmake")):
            return file_name[: -len(suffix)]
    return None


def built_packages(model: dict) -> dict[str, str]:
    """CMake package names SonicDE's packages install, mapped to the package that installs each:
    from install manifests where a package was built, and from its sources' <Name>Config.cmake.in
    templates, so a provider that hasn't been built yet still counts."""
    packages: dict[str, str] = {}
    build_dir = Path(model["build_dir"])
    source_root = Path(model["source_root"])
    for name in model["topo_order"]:
        if model["packages"][name].get("type") == "system":
            continue
        file_names: list[str] = []
        manifest = build_dir / name / "install_manifest.txt"
        if manifest.is_file():
            file_names += [Path(line).name for line in manifest.read_text(errors="replace").splitlines()]
        source = source_root / name
        if source.is_dir():
            for depth in ("*", "*/*", "*/*/*"):
                file_names += [path.name[: -len(".in")] for path in source.glob(f"{depth}.cmake.in")]
        for file_name in file_names:
            package = _config_package(file_name)
            if package:
                packages.setdefault(package, name)
                packages.setdefault(package.lower(), name)
    return packages


def cache_directories(cache: Path) -> dict[str, str]:
    """<Name>_DIR entries of a CMakeCache.txt, by package name."""
    entries: dict[str, str] = {}
    for line in cache.read_text(errors="replace").splitlines():
        key, _, value = line.partition("=")
        name, _, kind = key.partition(":")
        if kind == "PATH" and name.endswith("_DIR") and value and not value.endswith("-NOTFOUND"):
            entries[name[: -len("_DIR")]] = value
    return entries


def problems(model: dict, stage_root: Path) -> list[str]:
    staged = built_packages(model)
    stage = stage_root.resolve()
    build_dir = Path(model["build_dir"])
    found: list[str] = []
    for name in model["topo_order"]:
        if model["packages"][name].get("type") == "system":
            continue
        cache = build_dir / name / "CMakeCache.txt"
        if not cache.is_file():
            continue
        for package, directory in sorted(cache_directories(cache).items()):
            provided = staged.get(package) or staged.get(package.lower())
            if provided is None:
                continue
            resolved = Path(directory).resolve()
            if resolved == stage or stage in resolved.parents:
                continue
            found.append(f"[{name}] found {package} in {directory}, not in the stage, where {provided} installs it")
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-model", default="state/model.json")
    parser.add_argument("-stage", default="state/install-stage")
    args = parser.parse_args(argv)
    try:
        found = problems(json.loads(Path(args.model).read_text()), Path(args.stage))
    except (OSError, ValueError, KeyError) as error:
        print(f"Staged package check failed: {error}", file=sys.stderr)
        return 1
    if found:
        print("Staged package check failed:\n" + "\n".join(found), file=sys.stderr)
        print("Each of these packages needs the SonicDE package that provides it in its manifest's\n"
              "depends. CMake keeps a found directory in the cache, so after fixing the manifest remove\n"
              "that package's build directory before building again. Nothing was installed.",
              file=sys.stderr)
        return 1
    print("Staged package check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
