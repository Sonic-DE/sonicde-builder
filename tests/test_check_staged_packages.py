# SPDX-License-Identifier: GPL-2.0-or-later
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_staged_packages import problems


def make_tree(tmp_path, cache_lines, provider_template=True, provider_manifest=None):
    """A build with a provider of KF6Foo and a consumer whose CMake cache holds cache_lines."""
    build, source, stage = tmp_path / "build", tmp_path / "src", tmp_path / "stage"
    (source / "sonicde/provider").mkdir(parents=True)
    if provider_template:
        (source / "sonicde/provider/KF6FooConfig.cmake.in").write_text("# fixture\n")
    (build / "sonicde/provider").mkdir(parents=True)
    if provider_manifest:
        (build / "sonicde/provider/install_manifest.txt").write_text("\n".join(provider_manifest) + "\n")
    (build / "sonicde/consumer").mkdir(parents=True)
    (build / "sonicde/consumer/CMakeCache.txt").write_text("\n".join(cache_lines) + "\n")
    (stage / "usr/lib/cmake/KF6Foo").mkdir(parents=True)
    model = {"build_dir": str(build), "source_root": str(source), "install_prefix": "/usr",
             "topo_order": ["os-installed/qt", "sonicde/provider", "sonicde/consumer"],
             "packages": {"os-installed/qt": {"type": "system"}, "sonicde/provider": {"type": ""},
                          "sonicde/consumer": {"type": ""}}}
    return model, stage


def test_sonicde_package_found_outside_the_stage_is_reported(tmp_path):
    model, stage = make_tree(tmp_path, ["KF6Foo_DIR:PATH=/usr/lib64/cmake/KF6Foo"])
    found = problems(model, stage)
    assert len(found) == 1
    assert "[sonicde/consumer] found KF6Foo in /usr/lib64/cmake/KF6Foo" in found[0]
    assert "sonicde/provider" in found[0]


def test_provider_known_from_its_install_manifest(tmp_path):
    model, stage = make_tree(tmp_path, ["KF6Foo_DIR:PATH=/usr/lib64/cmake/KF6Foo"], provider_template=False,
                             provider_manifest=["/usr/lib/cmake/KF6Foo/KF6FooConfig.cmake",
                                                "/usr/lib/cmake/KF6Foo/KF6FooConfigVersion.cmake"])
    assert len(problems(model, stage)) == 1


def test_package_found_in_the_stage_passes(tmp_path):
    model, stage = make_tree(tmp_path, [f"KF6Foo_DIR:PATH={tmp_path / 'stage/usr/lib/cmake/KF6Foo'}"])
    assert problems(model, stage) == []


def test_packages_sonicde_does_not_build_may_come_from_the_system(tmp_path):
    model, stage = make_tree(tmp_path, ["Qt6Core_DIR:PATH=/usr/lib64/cmake/Qt6Core", "KF6Foo_DIR:PATH=KF6Foo_DIR-NOTFOUND"])
    assert problems(model, stage) == []
