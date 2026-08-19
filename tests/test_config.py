#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test configuration parsing, closure resolution, and validation."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from config import Project, ConfigError


class TestConfigParsing(unittest.TestCase):
    """Test YAML parsing, interpolation, and closure resolution."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_solution(self, packages_dir: Path, **extra) -> Path:
        sol = {
            "install-prefix": str(self.root / "prefix"),
            "build": [],
            "packages": [str(packages_dir)],
            "package-defaults": {},
        }
        sol.update(extra)
        sol_path = self.root / "solution.yaml"
        sol_path.write_text(
            __import__("yaml").safe_dump(sol, default_flow_style=False))
        return sol_path

    def _make_pkg(self, packages_dir: Path, name: str, **fields) -> Path:
        ns, _, basename = name.partition("/")
        pkg_dir = packages_dir / ns
        pkg_dir.mkdir(parents=True, exist_ok=True)
        pkg_file = pkg_dir / f"{basename}.yaml"
        pkg_file.write_text(
            __import__("yaml").safe_dump(fields, default_flow_style=False))
        return pkg_file

    def test_exact_package_resolution(self):
        """Exact package identity is resolved first."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "sonicde/foo", buildsystem="cmake")
        sol = self._make_solution(pdir, build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        self.assertIn("sonicde/foo", cl.packages)

    def test_provides_resolution(self):
        """Unique provides entry resolves when no exact match."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "os-installed/foo",
                       provides="sys/foo", type="system",
                       **{"pkg-config": "foo"})
        sol = self._make_solution(pdir, build=["sys/foo"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        self.assertIn("os-installed/foo", cl.packages)

    def test_ambiguous_provider_fails(self):
        """Ambiguous provider raises an error."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "a/foo", provides="sys/foo", type="system")
        self._make_pkg(pdir, "b/foo", provides="sys/foo", type="system")
        sol = self._make_solution(pdir, build=["sys/foo"])
        prj = Project(self.root, sol)
        prj.load()
        with self.assertRaises(ConfigError) as ctx:
            prj.build_closure()
        self.assertIn("ambiguous", str(ctx.exception))

    def test_missing_dependency_fails(self):
        """Missing dependency raises an error."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "sonicde/foo",
                       buildsystem="cmake",
                       depends=["sonicde/nonexistent"])
        sol = self._make_solution(pdir, build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        with self.assertRaises(ConfigError):
            prj.build_closure()

    def test_cycle_detection(self):
        """Dependency cycles are detected and reported."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "sonicde/a",
                       buildsystem="cmake", depends=["sonicde/b"])
        self._make_pkg(pdir, "sonicde/b",
                       buildsystem="cmake", depends=["sonicde/a"])
        sol = self._make_solution(pdir, build=["sonicde/a"])
        prj = Project(self.root, sol)
        prj.load()
        with self.assertRaises(ConfigError) as ctx:
            prj.build_closure()
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_qt6_source_rejected(self):
        """Active qt6/* source packages are rejected."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "qt6/qtbase",
                       buildsystem="cmake",
                       **{"sources": {"git": {
                           "url": "https://example.com/qtbase.git",
                           "ref": "v6.7.0"}}})
        sol = self._make_solution(pdir, build=["qt6/qtbase"])
        prj = Project(self.root, sol)
        prj.load()
        with self.assertRaises(ConfigError) as ctx:
            prj.build_closure()
        self.assertIn("Qt source", str(ctx.exception))

    def test_unsupported_buildsystem_rejected(self):
        """Active unsupported build systems are rejected."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "sonicde/foo",
                       buildsystem="meson",
                       **{"sources": {"git": {
                           "url": "https://example.com/foo.git",
                           "ref": "master"}}})
        sol = self._make_solution(pdir, build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        with self.assertRaises(ConfigError) as ctx:
            prj.build_closure()
        self.assertIn("unsupported buildsystem", str(ctx.exception))

    def test_interpolation_basename(self):
        """${@basename} is interpolated correctly."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "sonicde/myrepo",
                       buildsystem="cmake",
                       master_branch="master",
                       **{"sources": {"git": {
                           "ref": "origin/${master_branch}",
                           "remotes": {"origin": {
                               "url": "https://example.com/${@basename}.git",
                               "fetch": ["refs/heads/*:refs/remotes/origin/*"],
                           }}}}})
        sol = self._make_solution(pdir, build=["sonicde/myrepo"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        pkg = cl.packages["sonicde/myrepo"]
        self.assertEqual(pkg.git.remotes["origin"].url,
                         "https://example.com/myrepo.git")
        self.assertEqual(pkg.git.ref, "origin/master")

    def test_interpolation_solution_nested_list(self):
        """${@SOLUTION::fetch::origin} resolves to a list, not a string."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "sonicde/myrepo",
                       buildsystem="cmake",
                       master_branch="master",
                       **{"sources": {"git": {
                           "ref": "origin/${master_branch}",
                           "remotes": {"origin": {
                               "url": "https://example.com/${@basename}.git",
                               "fetch": "${@SOLUTION::fetch::origin}",
                           }}}}})
        sol = self._make_solution(
            pdir,
            build=["sonicde/myrepo"],
            **{"fetch": {"origin": [
                "refs/heads/*:refs/remotes/origin/*",
                "refs/tags/*:refs/tags/origin/*",
            ]}})
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        pkg = cl.packages["sonicde/myrepo"]
        self.assertEqual(pkg.git.remotes["origin"].fetch,
                         ["refs/heads/*:refs/remotes/origin/*",
                          "refs/tags/*:refs/tags/origin/*"])

    def test_package_mapping(self):
        """Explicit package-mapping is applied before exact/provider lookup."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "os-installed/libdrm",
                       provides="sys/libdrm", type="system",
                       **{"pkg-config": "libdrm"})
        self._make_pkg(pdir, "sonicde/foo",
                       buildsystem="cmake",
                       depends=["sys/libdrm"])
        sol = self._make_solution(
            pdir,
            build=["sonicde/foo"],
            **{"package-mapping": {"sys/libdrm": "os-installed/libdrm"}})
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        self.assertIn("os-installed/libdrm", cl.packages)

    def test_model_serialization(self):
        """Model JSON is written atomically."""
        pdir = self.root / "pkgs"
        self._make_pkg(pdir, "sonicde/foo", buildsystem="cmake")
        sol = self._make_solution(pdir, build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        out = self.root / "model.json"
        prj.write_model(cl, out)
        model = json.loads(out.read_text())
        self.assertEqual(model["schema_version"], 1)
        self.assertIn("sonicde/foo", model["packages"])


if __name__ == "__main__":
    unittest.main()
