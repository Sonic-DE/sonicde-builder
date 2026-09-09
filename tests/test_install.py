# SPDX-License-Identifier: GPL-2.0-only
"""Install test fixtures only into temporary directories, never the host system."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate import generate_cmake
from install import install_directories, main


def test_system_destination_untouched_until_meta_build_succeeds(tmp_path, monkeypatch):
    src = tmp_path / "src"
    build = tmp_path / "build"
    destination = tmp_path / "final prefix"
    stage = tmp_path / "private stage"
    for name in ("prereq", "consumer"):
        directory = src / name
        directory.mkdir(parents=True)
        (directory / "payload.txt").write_text(name)
        if name == "prereq":
            (directory / "FixtureConfig.cmake").write_text("set(Fixture_FOUND TRUE)\n")
        (directory / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.28)\nproject(Fixture NONE)\n"
            + ("install(FILES FixtureConfig.cmake DESTINATION lib/cmake/Fixture)\n" if name == "prereq" else
               "find_package(Fixture REQUIRED)\nadd_custom_target(verify ALL COMMAND ${CMAKE_COMMAND} -E false)\n")
            + f"install(FILES payload.txt DESTINATION share/{name})\n"
        )
    model = {
        "packages": {name: {"buildsystem": "cmake"} for name in ("prereq", "consumer")},
        "topo_order": ["prereq", "consumer"],
        "edges": [{"dependent": "consumer", "prerequisite": "prereq", "kind": "depends"}],
        "source_root": str(src), "build_dir": str(build), "install_prefix": str(destination),
    }
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(model))
    generated = tmp_path / "SonicDEProjects.cmake"
    generate_cmake(model, generated, staging_root=stage)
    (tmp_path / "CMakeLists.txt").write_text(
        'cmake_minimum_required(VERSION 3.28)\nproject(Meta NONE)\ninclude(SonicDEProjects.cmake)\n')
    env = dict(os.environ)
    env.pop("DESTDIR", None)
    subprocess.run(["cmake", "-S", str(tmp_path), "-B", str(build), "-G", "Ninja"], env=env, check=True, capture_output=True)
    failed = subprocess.run(["cmake", "--build", str(build)], env=env, capture_output=True, text=True)
    assert failed.returncode != 0
    assert not destination.exists()
    assert (stage / destination.relative_to("/") / "share/prereq/payload.txt").is_file()

    # Fix the consumer, build ALL, and still expect nothing in the final prefix.
    consumer = src / "consumer/CMakeLists.txt"
    consumer.write_text(consumer.read_text().replace("-E false", "-E true"))
    subprocess.run(["cmake", "--build", str(build)], env=env, check=True, capture_output=True)
    assert not destination.exists()
    assert (stage / destination.relative_to("/") / "share/consumer/payload.txt").is_file()

    # Only the explicit, post-build deployment populates the final destination.
    monkeypatch.delenv("DESTDIR", raising=False)
    assert main(["-model", str(model_path)]) == 0
    assert (destination / "share/prereq/payload.txt").read_text() == "prereq"
    assert (destination / "share/consumer/payload.txt").read_text() == "consumer"


def test_staged_tool_finds_staged_xdg_data(tmp_path):
    src = tmp_path / "src"
    stage = tmp_path / "stage"
    package_builds = tmp_path / "package-builds"
    meta_build = tmp_path / "meta-build"
    prereq = src / "prereq"
    consumer = src / "consumer"
    prereq.mkdir(parents=True)
    consumer.mkdir(parents=True)
    tool = prereq / "staged-data-tool"
    tool.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "roots = os.environ.get('XDG_DATA_DIRS', '').split(':')\n"
        "sys.exit(0 if any((pathlib.Path(p) / 'fixture/catalog.xml').is_file() for p in roots if p) else 23)\n")
    (prereq / "catalog.xml").write_text("catalog\n")
    (prereq / "FixtureConfig.cmake").write_text("set(Fixture_FOUND TRUE)\n")
    (prereq / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.28)\nproject(Prereq NONE)\n"
        "install(PROGRAMS staged-data-tool DESTINATION bin)\n"
        "install(FILES catalog.xml DESTINATION share/fixture)\n"
        "install(FILES FixtureConfig.cmake DESTINATION lib/cmake/Fixture)\n")
    (consumer / "payload.txt").write_text("consumer\n")
    (consumer / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.28)\nproject(Consumer NONE)\n"
        "find_package(Fixture REQUIRED)\n"
        "find_program(STAGED_DATA_TOOL staged-data-tool REQUIRED)\n"
        "add_custom_target(verify ALL COMMAND ${STAGED_DATA_TOOL})\n"
        "install(FILES payload.txt DESTINATION share/consumer)\n")
    model = {
        "packages": {"prereq": {"buildsystem": "cmake"}, "consumer": {"buildsystem": "cmake"}},
        "topo_order": ["prereq", "consumer"],
        "edges": [{"dependent": "consumer", "prerequisite": "prereq", "kind": "depends"}],
        "source_root": str(src), "build_dir": str(package_builds), "install_prefix": "/usr",
    }
    generated = tmp_path / "SonicDEProjects.cmake"
    generate_cmake(model, generated, staging_root=stage)
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.28)\nproject(Meta NONE)\ninclude(SonicDEProjects.cmake)\n")
    subprocess.run(["cmake", "-S", str(tmp_path), "-B", str(meta_build), "-G", "Ninja"],
                   check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(meta_build)], check=True, capture_output=True)
    assert (stage / "usr/share/fixture/catalog.xml").is_file()
    assert (stage / "usr/share/consumer/payload.txt").is_file()


def test_staged_exported_target_resolves_to_staged_library(tmp_path):
    src = tmp_path / "src"
    prereq = src / "prereq"
    consumer = src / "consumer"
    prereq.mkdir(parents=True)
    consumer.mkdir(parents=True)
    (prereq / "fixture.c").write_text("int fixture(void) { return 7; }\n")
    (prereq / "FixtureConfig.cmake").write_text("include(${CMAKE_CURRENT_LIST_DIR}/FixtureTargets.cmake)\n")
    (prereq / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.28)\nproject(Prereq C)\n"
        "add_library(fixture SHARED fixture.c)\n"
        "install(TARGETS fixture EXPORT FixtureTargets LIBRARY DESTINATION lib)\n"
        "install(EXPORT FixtureTargets NAMESPACE Fixture:: DESTINATION lib/cmake/Fixture)\n"
        "install(FILES FixtureConfig.cmake DESTINATION lib/cmake/Fixture)\n")
    (consumer / "main.c").write_text("int fixture(void); int main(void) { return fixture() != 7; }\n")
    (consumer / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.28)\nproject(Consumer C)\n"
        "find_package(Fixture CONFIG REQUIRED)\n"
        "add_executable(consumer main.c)\ntarget_link_libraries(consumer PRIVATE Fixture::fixture)\n"
        "install(TARGETS consumer RUNTIME DESTINATION bin)\n")
    stage = tmp_path / "stage"
    package_builds = tmp_path / "package-builds"
    model = {"packages": {"prereq": {"buildsystem": "cmake"}, "consumer": {"buildsystem": "cmake"}},
             "topo_order": ["prereq", "consumer"],
             "edges": [{"dependent": "consumer", "prerequisite": "prereq", "kind": "depends"}],
             "source_root": str(src), "build_dir": str(package_builds), "install_prefix": "/usr"}
    generate_cmake(model, tmp_path / "SonicDEProjects.cmake", staging_root=stage)
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.28)\nproject(Meta NONE)\ninclude(SonicDEProjects.cmake)\n")
    subprocess.run(["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "meta"), "-G", "Ninja"],
                   check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(tmp_path / "meta")], check=True, capture_output=True)
    assert (stage / "usr/lib/libfixture.so").is_file()
    assert (stage / "usr/bin/consumer").is_file()
    targets = (stage / "usr/lib/cmake/Fixture/FixtureTargets-noconfig.cmake").read_text()
    assert '"${_IMPORT_PREFIX}/lib/libfixture.so"' in targets
    assert '"/usr/lib/libfixture.so"' not in targets


def test_absolute_install_destination_is_redirected_before_full_build(tmp_path):
    src = tmp_path / "src/pkg"
    src.mkdir(parents=True)
    final_prefix = tmp_path / "final"
    stage = tmp_path / "stage"
    build_root = tmp_path / "packages"
    (src / "payload.py").write_text("value = 1\n")
    (src / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.28)\nproject(AbsoluteInstall NONE)\n"
        "install(FILES payload.py DESTINATION \"${CMAKE_INSTALL_PREFIX}/lib/python3.14/site-packages/fixture\")\n")
    model = {"packages": {"pkg": {"buildsystem": "cmake"}}, "topo_order": ["pkg"], "edges": [],
             "source_root": str(tmp_path / "src"), "build_dir": str(build_root),
             "install_prefix": str(final_prefix)}
    generate_cmake(model, tmp_path / "SonicDEProjects.cmake", staging_root=stage)
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.28)\nproject(Meta NONE)\ninclude(SonicDEProjects.cmake)\n")
    subprocess.run(["cmake", "-S", str(tmp_path), "-B", str(tmp_path / "meta"), "-G", "Ninja"],
                   check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(tmp_path / "meta")], check=True, capture_output=True)
    relative_prefix = final_prefix.relative_to("/")
    assert (stage / relative_prefix / "lib/python3.14/site-packages/fixture/payload.py").is_file()
    assert not final_prefix.exists()
    cache_lines = (build_root / "pkg/CMakeCache.txt").read_text().splitlines()
    staging_values = [line.split("=", 1)[1] for line in cache_lines
                      if line.startswith("CMAKE_STAGING_PREFIX:")]
    assert staging_values == []


def test_preflight_checks_all_packages_before_install(tmp_path, monkeypatch):
    first = tmp_path / "first"
    first.mkdir()
    (first / "CMakeCache.txt").write_text("CMAKE_INSTALL_PREFIX:PATH=/usr\n")
    (first / "cmake_install.cmake").touch()
    model = {"build_dir": str(tmp_path), "install_prefix": "/usr", "topo_order": ["first", "missing"],
             "packages": {"first": {"buildsystem": "cmake"}, "missing": {"buildsystem": "cmake"}}}
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model))
    calls = []
    monkeypatch.setattr("install.subprocess.run", lambda *args, **kwargs: calls.append(args))
    assert main(["-model", str(path)]) == 1
    assert calls == []


def test_prefix_mismatch_is_rejected(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "CMakeCache.txt").write_text("CMAKE_INSTALL_PREFIX:PATH=/unexpected\n")
    (package / "cmake_install.cmake").touch()
    model = {"build_dir": str(tmp_path), "install_prefix": "/usr", "topo_order": ["pkg"],
             "packages": {"pkg": {"buildsystem": "cmake"}}}
    with pytest.raises(ValueError, match="prefix differs"):
        install_directories(model)


def test_system_and_no_build_packages_are_not_installed(tmp_path):
    model = {"build_dir": str(tmp_path), "install_prefix": "/usr", "topo_order": ["sys", "data"],
             "packages": {"sys": {"type": "system"}, "data": {"buildsystem": "none"}}}
    assert install_directories(model) == []


def test_deployment_stops_on_first_error(tmp_path, monkeypatch):
    for name in ("a", "b"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "CMakeCache.txt").write_text("CMAKE_INSTALL_PREFIX:PATH=/usr\n")
        (directory / "cmake_install.cmake").touch()
    model = {"build_dir": str(tmp_path), "install_prefix": "/usr", "topo_order": ["a", "b"],
             "packages": {"a": {"buildsystem": "cmake"}, "b": {"buildsystem": "cmake"}}}
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model))
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("install.subprocess.run", fail)
    assert main(["-model", str(path)]) == 1
    assert calls == [["cmake", "--install", str(tmp_path / "a")]]
