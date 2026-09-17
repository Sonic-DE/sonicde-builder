# SPDX-License-Identifier: GPL-2.0-only
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from combined_graph_labels import combined_labels


ROOT = Path(__file__).resolve().parents[1]


def test_combined_labels_include_the_source_repository():
    graph = {
        "nodes": [
            {"community": 7, "local_community": 2, "community_name": "Window Management",
             "repo": "sonic-workspace"},
            {"community": 8, "local_community": 0, "repo": "sonic-win"},
        ]
    }

    assert combined_labels(graph) == {
        "7": "sonic-workspace: Window Management",
        "8": "sonic-win: Community 0",
    }


def test_merge_graphs_generates_combined_html(tmp_path):
    script = tmp_path / "merge-graphs"
    shutil.copy2(ROOT / "merge-graphs", script)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(ROOT / "scripts/combined_graph_labels.py", scripts_dir)

    for group, project in (("sonicde", "alpha"), ("3rdparty", "beta")):
        graph = tmp_path / "src" / group / project / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True)
        graph.write_text("{}\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_graphify = bin_dir / "graphify"
    fake_graphify.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "root = pathlib.Path.cwd()\n"
        "with (root / 'calls.log').open('a') as log:\n"
        "    limit = os.environ.get('GRAPHIFY_VIZ_NODE_LIMIT', '')\n"
        "    log.write(' '.join(sys.argv[1:]) + ' | limit=' + limit + '\\n')\n"
        "if sys.argv[1] == 'merge-graphs':\n"
        "    out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\n"
        "    out.parent.mkdir(parents=True, exist_ok=True)\n"
        "    out.write_text('{\\\"nodes\\\": [{\\\"id\\\": \\\"alpha::a\\\", "
        "\\\"repo\\\": \\\"alpha\\\", \\\"community\\\": 0, "
        "\\\"community_name\\\": \\\"Alpha Core\\\"}], \\\"links\\\": []}\\n')\n"
        "elif sys.argv[1:3] == ['export', 'html']:\n"
        "    graph = pathlib.Path(sys.argv[sys.argv.index('--graph') + 1])\n"
        "    (graph.parent / 'graph.html').write_text('<html></html>\\n')\n"
    )
    fake_graphify.chmod(0o755)

    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    result = subprocess.run([str(script)], cwd=tmp_path, env=env, text=True,
                            capture_output=True, check=True)

    assert (tmp_path / "graphify-out/graph.json").is_file()
    assert (tmp_path / "graphify-out/graph.html").is_file()
    labels = (tmp_path / "graphify-out/.graphify_labels.json").read_text()
    assert '\"0\": \"alpha: Alpha Core\"' in labels
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls[0].startswith("merge-graphs ")
    assert "--out graphify-out/graph.json | limit=" in calls[0]
    assert calls[1] == (
        "export html --graph graphify-out/graph.json "
        "--labels graphify-out/.graphify_labels.json | limit=20000"
    )
    assert "graphify-out/graph.json and graphify-out/graph.html" in result.stdout
