# SPDX-License-Identifier: GPL-2.0-only
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_sources import main, source_problems


def model(root):
    return {"source_root": str(root), "topo_order": ["sys", "sonicde/a", "sonicde/b", "data"],
            "packages": {"sys": {"type": "system"},
                         "sonicde/a": {"buildsystem": "cmake"},
                         "sonicde/b": {"buildsystem": "cmake"},
                         "data": {"buildsystem": "none"}}}


def test_lists_all_missing_sources_without_mutation(tmp_path, capsys):
    source_root = tmp_path / "sources"
    empty_checkout = source_root / "sonicde/b"
    (empty_checkout / ".git").mkdir(parents=True)
    (source_root / "data").mkdir()
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model(source_root)))
    assert main(["-model", str(path)]) == 1
    diagnostic = capsys.readouterr().err
    assert "[sonicde/a] missing source directory" in diagnostic
    assert "[sonicde/b] missing CMakeLists.txt" in diagnostic
    assert "[sys]" not in diagnostic
    assert "[data]" not in diagnostic
    assert "./fetch-all" in diagnostic
    assert not (source_root / "sonicde/a").exists()
    assert not (empty_checkout / "CMakeLists.txt").exists()


def test_complete_cmake_and_no_build_sources_pass(tmp_path):
    for name in ("sonicde/a", "sonicde/b"):
        package = tmp_path / name
        package.mkdir(parents=True)
        (package / "CMakeLists.txt").write_text("project(Fixture NONE)\n")
    (tmp_path / "data").mkdir()
    assert source_problems(model(tmp_path)) == []


def test_missing_no_build_source_is_reported(tmp_path):
    problems = source_problems(model(tmp_path))
    assert any("[data] missing source directory" in problem for problem in problems)
