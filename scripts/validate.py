#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""
Validate active system dependencies and Qt6 Core for the SonicDE builder.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def validate_pkg_config(pkg_name: str, modules: list[str]) -> int:
    """Run pkg-config --modversion for each declared module."""
    if not modules:
        # system package with no pkg-config entry is a no-op (matches MPBT)
        return 0
    errors = 0
    for mod in modules:
        r = subprocess.run(["pkg-config", "--modversion", mod],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[{pkg_name}] missing pkg-config module: {mod}", file=sys.stderr)
            errors += 1
        else:
            print(f"[{pkg_name}] {mod}: {r.stdout.strip()}")
    return errors


def validate_python_modules(pkg_name: str, modules: list[str]) -> int:
    """Import required Python build modules using the active python3."""
    errors = 0
    for module in modules:
        result = subprocess.run([sys.executable, "-c", f"import {module}"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[{pkg_name}] missing Python module: {module}", file=sys.stderr)
            errors += 1
        else:
            print(f"[{pkg_name}] Python module: {module}")
    return errors


def validate_commands(pkg_name: str, commands: list[str]) -> int:
    errors = 0
    for command in commands:
        path = shutil.which(command)
        if path is None:
            print(f"[{pkg_name}] missing executable: {command}", file=sys.stderr)
            errors += 1
        else:
            print(f"[{pkg_name}] executable: {command}: {path}")
    return errors


def validate_headers(pkg_name: str, headers: list[str]) -> int:
    roots = [Path("/usr/include"), Path("/usr/local/include")]
    roots.extend(Path(path) for path in os.environ.get("CPATH", "").split(":") if path)
    errors = 0
    for header in headers:
        found = next((root / header for root in roots if (root / header).is_file()), None)
        if found is None:
            print(f"[{pkg_name}] missing header: {header}", file=sys.stderr)
            errors += 1
        else:
            print(f"[{pkg_name}] header: {header}: {found}")
    return errors


def validate_cmake_packages(pkg_name: str, packages: list[str]) -> int:
    errors = 0
    for package in packages:
        with tempfile.TemporaryDirectory(prefix="sonicde-cmake-probe-") as temporary:
            root = Path(temporary)
            (root / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.28)\n"
                "project(SonicDECMakeProbe LANGUAGES CXX)\n"
                f"find_package({package} CONFIG REQUIRED)\n")
            result = subprocess.run(["cmake", "-S", str(root), "-B", str(root / "build")],
                                    capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[{pkg_name}] missing CMake package: {package}", file=sys.stderr)
            errors += 1
        else:
            print(f"[{pkg_name}] CMake package: {package}")
    return errors


def validate_system_packages(model: dict) -> int:
    """Validate all active system packages."""
    errors = 0
    for name, pkg in sorted(model["packages"].items()):
        if pkg.get("type") != "system":
            continue
        modules = pkg.get("pkg_config") or []
        if isinstance(modules, str):
            modules = [modules]
        errors += validate_pkg_config(name, modules)
        python_modules = pkg.get("python_modules") or []
        if isinstance(python_modules, str):
            python_modules = [python_modules]
        errors += validate_python_modules(name, python_modules)
        commands = pkg.get("commands") or []
        if isinstance(commands, str):
            commands = [commands]
        errors += validate_commands(name, commands)
        headers = pkg.get("headers") or []
        if isinstance(headers, str):
            headers = [headers]
        errors += validate_headers(name, headers)
        cmake_packages = pkg.get("cmake_packages") or []
        if isinstance(cmake_packages, str):
            cmake_packages = [cmake_packages]
        errors += validate_cmake_packages(name, cmake_packages)
    return errors


def validate_qt_core(model: dict) -> int:
    """Configure a real C++ CMake project that calls find_package(Qt6 Core)."""
    install_prefix = model.get("install_prefix", "")
    qt_probe = """cmake_minimum_required(VERSION 3.28)
project(SonicDEQtProbe LANGUAGES CXX)
find_package(Qt6 REQUIRED COMPONENTS Core)
message(STATUS "Qt6 version: ${Qt6Core_VERSION}")
message(STATUS "Qt6_DIR: ${Qt6_DIR}")
"""
    with tempfile.TemporaryDirectory(prefix="sonicde-qt-probe-") as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "CMakeLists.txt").write_text(qt_probe)
        build = tmp / "build"
        build.mkdir()
        args = ["cmake", str(tmp)]
        if install_prefix:
            args.append(f"-DCMAKE_PREFIX_PATH={install_prefix}")
        r = subprocess.run(args + ["-B", str(build)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("Qt6 Core probe FAILED:", file=sys.stderr)
            print(r.stderr, file=sys.stderr)
            return 1
        # print the status lines from the output
        for line in r.stdout.splitlines():
            if "Qt6" in line:
                print(line)
    return 0


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Validate system deps and Qt6")
    ap.add_argument("-model", default="state/model.json")
    ap.add_argument("--skip-qt", action="store_true")
    args = ap.parse_args(argv)

    with open(args.model) as f:
        model = json.load(f)

    errs = validate_system_packages(model)
    if errs:
        print(f"\n{errs} system dependency failure(s)", file=sys.stderr)
        return 1

    if not args.skip_qt:
        errs = validate_qt_core(model)
        if errs:
            return 1

    print("validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
