#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Pick the OS package names for the active system packages of a model."""
from __future__ import annotations

import json
import platform
import sys
from typing import Any

# Ships with the OS itself (FreeBSD base system): never printed.
BASE_SYSTEM = "base"


def os_release_id() -> str:
    try:
        return platform.freedesktop_os_release()["ID"]
    except (AttributeError, OSError, KeyError):
        return ""


def select_packages(model: dict[str, Any], os_id: str) -> tuple[list[str], list[str]]:
    """Return (package names, system packages with no name for this platform)."""
    on_freebsd = model.get("platform") == "freebsd"
    if on_freebsd:
        keys = [k for k in (os_id, "freebsd") if k]
    else:
        keys = [os_id or "debian", "debian"]
    names: set[str] = set()
    missing: list[str] = []
    for ident, pkg in sorted(model["packages"].items()):
        if pkg.get("type") != "system":
            continue
        pp = pkg.get("platform_packages") or {}
        name = next((pp[k] for k in keys if pp.get(k)), "")
        if name == BASE_SYSTEM:
            continue
        if name:
            names.add(name)
        else:
            missing.append(ident)
    return sorted(names), missing


def main(argv: list[str]) -> int:
    model = json.load(open(argv[0] if argv else "state/model.json"))
    names, missing = select_packages(model, os_release_id())
    for ident in missing:
        print(f"no {model.get('platform', 'linux')} package name for {ident}", file=sys.stderr)
    if names:
        print(" ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
