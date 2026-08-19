#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test CMake ExternalProject generation."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate import generate_cmake, generate_dot, sanitize_target


class TestGenerateCMake(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _model(self, packages: dict, edges: list, topo: list) -> dict:
        return {
            "packages": packages,
            "edges": edges,
            "topo_order": topo,
            "source_root": str(self.root / "src"),
            "build_dir": str(self.root / "build"),
            "install_prefix": str(self.root / "prefix"),
        }

    def test_cmake_targets_generated(self):
        """CMake file contains ExternalProject targets for CMake packages."""
        model = self._model(
            {"sonicde/a": {"buildsystem": "cmake", "git": {}},
             "sonicde/b": {"buildsystem": "cmake", "git": {}}},
            [{"dependent": "sonicde/b", "prerequisite": "sonicde/a",
              "kind": "depends"}],
            ["sonicde/a", "sonicde/b"])
        out = self.root / "SonicDEProjects.cmake"
        generate_cmake(model, out)
        text = out.read_text()
        self.assertIn("ExternalProject_add", text)
        self.assertIn(sanitize_target("sonicde/a"), text)
        self.assertIn(sanitize_target("sonicde/b"), text)
        self.assertIn("DEPENDS", text)

    def test_system_package_validation_target(self):
        """System packages become custom validation targets, not ExternalProject."""
        model = self._model(
            {"os-installed/foo": {"type": "system", "pkg_config": ["foo"]}},
            [],
            ["os-installed/foo"])
        out = self.root / "out.cmake"
        generate_cmake(model, out)
        text = out.read_text()
        self.assertIn("add_custom_target", text)
        self.assertNotIn("ExternalProject_add", text)

    def test_no_build_target(self):
        """No-build packages become no-op custom targets."""
        model = self._model(
            {"sonicde/l10n": {"buildsystem": "none", "git": {}}},
            [],
            ["sonicde/l10n"])
        out = self.root / "out.cmake"
        generate_cmake(model, out)
        text = out.read_text()
        self.assertIn("add_custom_target", text)
        self.assertNotIn("ExternalProject_add", text)

    def test_dot_graph(self):
        """DOT graph preserves dependent -> prerequisite direction."""
        model = self._model(
            {"sonicde/a": {"buildsystem": "cmake"},
             "sonicde/b": {"buildsystem": "cmake"}},
            [{"dependent": "sonicde/b", "prerequisite": "sonicde/a",
              "kind": "depends"}],
            ["sonicde/a", "sonicde/b"])
        out = self.root / "graph.dot"
        generate_dot(model, out)
        text = out.read_text()
        self.assertIn('"sonicde/b" -> "sonicde/a"', text)

    def test_no_meson_target(self):
        """Meson packages (if any) are not emitted as ExternalProject."""
        model = self._model(
            {"3rdparty/libdrm": {"buildsystem": "meson", "git": {}}},
            [],
            ["3rdparty/libdrm"])
        out = self.root / "out.cmake"
        # generate_cmake should not emit ExternalProject for meson
        generate_cmake(model, out)
        text = out.read_text()
        self.assertNotIn("ExternalProject_add", text)

    def test_cmake_args_forwarded(self):
        """cmake-extra-args are forwarded to ExternalProject."""
        model = self._model(
            {"sonicde/a": {
                "buildsystem": "cmake", "git": {},
                "cmake_extra_args": ["-DBUILD_TESTING=OFF"]}},
            [],
            ["sonicde/a"])
        out = self.root / "out.cmake"
        generate_cmake(model, out)
        text = out.read_text()
        self.assertIn("-DBUILD_TESTING=OFF", text)
        self.assertIn("CMAKE_INSTALL_PREFIX", text)
        self.assertIn("CMAKE_PREFIX_PATH", text)


class TestCMakeIntegration(unittest.TestCase):
    """Integration test: fixture prerequisite + consumer via ExternalProject."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_prerequisite_before_consumer(self):
        """A prerequisite installs a config package that a consumer finds."""
        src = self.root / "src"
        prefix = self.root / "prefix"
        build = self.root / "build"

        # Prerequisite: installs FixtureConfig.cmake
        prereq = src / "prereq"
        prereq.mkdir(parents=True)
        (prereq / "CMakeLists.txt").write_text(f"""
cmake_minimum_required(VERSION 3.28)
project(FixturePrereq LANGUAGES CXX)
install(FILES FixtureConfig.cmake DESTINATION lib/cmake/Fixture)
""")
        (prereq / "FixtureConfig.cmake").write_text(
            "set(Fixture_FOUND TRUE)\n")

        # Consumer: find_package(Fixture REQUIRED)
        consumer = src / "consumer"
        consumer.mkdir(parents=True)
        (consumer / "CMakeLists.txt").write_text(f"""
cmake_minimum_required(VERSION 3.28)
project(FixtureConsumer LANGUAGES CXX)
find_package(Fixture REQUIRED)
install(FILES use.txt DESTINATION share)
""")
        (consumer / "use.txt").write_text("ok\n")

        model = {
            "packages": {
                "prereq": {"buildsystem": "cmake", "git": {}},
                "consumer": {"buildsystem": "cmake", "git": {},
                             "cmake_extra_args": []},
            },
            "edges": [{"dependent": "consumer",
                       "prerequisite": "prereq", "kind": "depends"}],
            "topo_order": ["prereq", "consumer"],
            "source_root": str(src),
            "build_dir": str(build),
            "install_prefix": str(prefix),
        }

        cmake_file = self.root / "SonicDEProjects.cmake"
        generate_cmake(model, cmake_file)
        top_cmake = self.root / "CMakeLists.txt"
        top_cmake.write_text(f"""
cmake_minimum_required(VERSION 3.28)
project(TestSuperbuild LANGUAGES CXX)
list(APPEND CMAKE_MODULE_PATH {self.root})
include(SonicDEProjects)
""")
        # Configure
        r = subprocess.run(
            ["cmake", "-B", str(build), "-S", str(self.root),
             f"-DCMAKE_PREFIX_PATH={prefix}"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

        # Build prereq first, then consumer
        r = subprocess.run(
            ["cmake", "--build", str(build), "--target",
             sanitize_target("prereq")],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

        # Verify prereq installed the config
        self.assertTrue((prefix / "lib" / "cmake" / "Fixture" /
                        "FixtureConfig.cmake").exists())

        r = subprocess.run(
            ["cmake", "--build", str(build), "--target",
             sanitize_target("consumer")],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
