#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Create repo-qualified community labels for a merged Graphify graph."""

import json
import os
import sys
import tempfile
from pathlib import Path


def combined_labels(graph: dict) -> dict[str, str]:
    labels: dict[str, str] = {}
    for node in graph.get("nodes", []):
        community = node.get("community")
        if not isinstance(community, int):
            continue
        key = str(community)
        if key in labels:
            continue
        repo = str(node.get("repo") or "unknown-repo")
        local = node.get("local_community", community)
        name = str(node.get("community_name") or f"Community {local}")
        labels[key] = f"{repo}: {name}"
    return labels


def write_labels(graph_path: Path, labels_path: Path) -> int:
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    labels = combined_labels(graph)
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{labels_path.name}.", dir=labels_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(labels, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, labels_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return len(labels)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: combined_graph_labels.py GRAPH_JSON LABELS_JSON", file=sys.stderr)
        return 2
    count = write_labels(Path(argv[0]), Path(argv[1]))
    print(f"Generated {count} repo-qualified community labels -> {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
