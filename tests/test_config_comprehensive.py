#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Comprehensive config tests mirroring real SonicDE solution structure."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from config import Project, ConfigError


class TestRealSolutionStructure(unittest.TestCase):
    """Test parsing with a solution structure matching the real SonicDE one."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_solution(self, **overrides) -> Path:
        sol = {
            "sonicde_git": "https://github.com/Sonic-DE",
            "kf6_git": "https://invent.kde.org/frameworks/",
            "plasma_git": "https://invent.kde.org/plasma/",
            "sonic_depth": 0,
            "upstream_depth": 0,
            "install-prefix": "${@PROJECT::@homedir}/SONICDE",
            "build": [],
            "env": {
                "PATH": "${install-prefix}/bin:/bin:/usr/bin",
                "PKG_CONFIG_PATH": "${install-prefix}/lib/pkgconfig",
            },
            "packages": ["${@PROJECT::@rootdir}/config/sonicde/packages"],
            "package-defaults": {
                "cmake-args": ["-GNinja"],
                "sonicde_git": "${sonicde_git}",
                "kf6_git": "${kf6_git}",
                "sonic_depth": "${sonic_depth}",
                "upstream_depth": "${upstream_depth}",
            },
            "fetch": {
                "origin": [
                    "refs/heads/*:refs/remotes/origin/*",
                    "refs/tags/*:refs/tags/origin/*",
                ],
                "upstream": [
                    "refs/heads/*:refs/remotes/upstream/*",
                    "refs/tags/*:refs/tags/upstream/*",
                ],
            },
        }
        sol.update(overrides)
        sol_path = self.root / "config" / "sonicde" / "solutions" / "sonicde.yaml"
        sol_path.parent.mkdir(parents=True, exist_ok=True)
        sol_path.write_text(yaml.safe_dump(sol, default_flow_style=False))
        return sol_path

    def _write_pkg(self, name: str, **fields) -> None:
        ns, _, basename = name.partition("/")
        pkg_dir = self.root / "config" / "sonicde" / "packages" / ns
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / f"{basename}.yaml").write_text(
            yaml.safe_dump(fields, default_flow_style=False))

    def test_full_sonic_package_with_upstream(self):
        """A typical SonicDE forked package with autopick config resolves."""
        self._write_pkg("sonicde/sonic-frameworks-core-addons",
            buildsystem="cmake",
            master_branch="master",
            **{"sources": {"git": {
                "ref": "origin/${master_branch}",
                "remotes": {
                    "origin": {
                        "url": "${sonicde_git}/${@basename}.git",
                        "depth": "${sonic_depth}",
                        "fetch": "${@SOLUTION::fetch::origin}",
                    },
                    "upstream": {
                        "url": "${kf6_git}/extra-cmake-modules.git",
                        "depth": "${upstream_depth}",
                        "fetch": "${@SOLUTION::fetch::upstream}",
                    },
                },
                "config": {
                    "autopick.enabled": "true",
                    "autopick.upstream": "upstream/master",
                    "autopick.master": "origin/${master_branch}",
                    "autopick.tracker": "origin/tracking/master",
                    "advice.detachedHead": "false",
                    "remote.origin.tagOpt": "--no-tags",
                    "remote.upstream.tagOpt": "--no-tags",
                },
                "local-branch": "${master_branch}",
            }}},
            **{"build-depends": ["sonicde/sonic-frameworks-cmake-modules"]},
            depends=["sonicde/sonic-frameworks-internationalization"])
        self._write_pkg("sonicde/sonic-frameworks-cmake-modules",
            buildsystem="cmake", master_branch="master",
            **{"sources": {"git": {
                "ref": "origin/${master_branch}",
                "remotes": {"origin": {
                    "url": "${sonicde_git}/${@basename}.git",
                    "fetch": "${@SOLUTION::fetch::origin}",
                }},
                "config": {"advice.detachedHead": "false",
                           "remote.origin.tagOpt": "--no-tags"},
                "local-branch": "${master_branch}",
            }}})
        self._write_pkg("sonicde/sonic-frameworks-internationalization",
            buildsystem="cmake", master_branch="master",
            **{"sources": {"git": {
                "ref": "origin/${master_branch}",
                "remotes": {"origin": {
                    "url": "${sonicde_git}/${@basename}.git",
                    "fetch": "${@SOLUTION::fetch::origin}",
                }},
                "config": {"advice.detachedHead": "false"},
                "local-branch": "${master_branch}",
            }}})

        sol = self._write_solution(
            build=["sonicde/sonic-frameworks-core-addons"])
        prj = Project(self.root, sol,
                      solution_defines={"sonicde_git": "https://github.com/Sonic-DE"})
        prj.load()
        cl = prj.build_closure()

        self.assertEqual(len(cl.packages), 3)
        self.assertIn("sonicde/sonic-frameworks-core-addons", cl.packages)
        self.assertIn("sonicde/sonic-frameworks-cmake-modules", cl.packages)
        self.assertIn("sonicde/sonic-frameworks-internationalization", cl.packages)

        # Verify the core package git spec
        pkg = cl.packages["sonicde/sonic-frameworks-core-addons"]
        self.assertEqual(pkg.git.ref, "origin/master")
        self.assertEqual(pkg.git.local_branch, "master")
        self.assertEqual(pkg.git.remotes["origin"].url,
                         "https://github.com/Sonic-DE/sonic-frameworks-core-addons.git")
        self.assertEqual(pkg.git.remotes["origin"].depth, 0)
        self.assertEqual(pkg.git.remotes["origin"].fetch,
                         ["refs/heads/*:refs/remotes/origin/*",
                          "refs/tags/*:refs/tags/origin/*"])
        self.assertEqual(pkg.git.remotes["upstream"].url,
                         "https://invent.kde.org/frameworks//extra-cmake-modules.git")
        self.assertEqual(pkg.git.config["autopick.enabled"], "true")
        self.assertEqual(pkg.git.config["remote.origin.tagOpt"], "--no-tags")

    def test_system_package_with_platform_packages(self):
        """System packages with pkg-config and platform-packages parse."""
        self._write_pkg("os-installed/libdrm",
            provides="sys/libdrm", type="system",
            **{"pkg-config": "libdrm"},
            **{"platform-packages": {"debian": "libdrm-dev",
                                      "devuan": "libdrm-dev"}})
        self._write_pkg("sonicde/foo",
            buildsystem="cmake", depends=["sys/libdrm"],
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/foo.git"}},
            }}})
        sol = self._write_solution(build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        pkg = cl.packages["os-installed/libdrm"]
        self.assertEqual(pkg.ptype, "system")
        self.assertEqual(pkg.pkg_config, ["libdrm"])
        self.assertEqual(pkg.platform_packages["debian"], "libdrm-dev")

    def test_build_cmake_args_extraction(self):
        """build.cmake.args are extracted and merged with cmake-extra-args."""
        self._write_pkg("sonicde/foo",
            buildsystem="cmake",
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/foo.git"}},
            }},
            "cmake-extra-args": ["-DFOO=ON"],
            "build": {"cmake": {"args": ["-DBAR=OFF"]}}})
        sol = self._write_solution(build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        pkg = cl.packages["sonicde/foo"]
        self.assertIn("-DFOO=ON", pkg.cmake_extra_args)
        self.assertIn("-DBAR=OFF", pkg.cmake_extra_args)

    def test_no_build_package(self):
        """buildsystem: none is accepted as a no-build package."""
        self._write_pkg("sonicde/l10n",
            buildsystem="none", master_branch="master",
            **{"sources": {"git": {
                "ref": "origin/${master_branch}",
                "remotes": {"origin": {
                    "url": "${sonicde_git}/${@basename}.git",
                    "fetch": "${@SOLUTION::fetch::origin}",
                }},
                "config": {"advice.detachedHead": "false"},
                "local-branch": "${master_branch}",
            }}})
        sol = self._write_solution(build=["sonicde/l10n"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        self.assertIn("sonicde/l10n", cl.packages)
        self.assertEqual(cl.packages["sonicde/l10n"].buildsystem, "none")

    def test_install_prefix_interpolation(self):
        """${@PROJECT::@homedir}/SONICDE resolves correctly."""
        self._write_pkg("sonicde/foo", buildsystem="cmake",
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/foo.git"}},
            }}})
        sol = self._write_solution(build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        expected = os.path.expanduser("~") + "/SONICDE"
        self.assertEqual(prj.scope["@installprefix"], expected)

    def test_env_vars_propagated(self):
        """Solution env section is available in the solution scope."""
        self._write_pkg("sonicde/foo", buildsystem="cmake",
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/foo.git"}},
            }}})
        sol = self._write_solution(build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        self.assertIn("PATH", prj.solution.get("env", {}))

    def test_topological_order(self):
        """Topological order puts prerequisites before dependents."""
        self._write_pkg("sonicde/a", buildsystem="cmake",
            depends=["sonicde/b"],
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/a.git"}},
            }}})
        self._write_pkg("sonicde/b", buildsystem="cmake",
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/b.git"}},
            }}})
        sol = self._write_solution(build=["sonicde/a"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        idx_a = cl.topo_order.index("sonicde/a")
        idx_b = cl.topo_order.index("sonicde/b")
        self.assertLess(idx_b, idx_a)

    def test_reverse_edges(self):
        """Reverse edges correctly map prerequisite -> dependents."""
        self._write_pkg("sonicde/a", buildsystem="cmake",
            depends=["sonicde/c"],
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/a.git"}},
            }}})
        self._write_pkg("sonicde/b", buildsystem="cmake",
            depends=["sonicde/c"],
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/b.git"}},
            }}})
        self._write_pkg("sonicde/c", buildsystem="cmake",
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/c.git"}},
            }}})
        sol = self._write_solution(build=["sonicde/a", "sonicde/b"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        dependents = cl.reverse.get("sonicde/c", [])
        self.assertIn("sonicde/a", dependents)
        self.assertIn("sonicde/b", dependents)

    def test_model_json_structure(self):
        """Model JSON contains all required top-level keys."""
        self._write_pkg("sonicde/foo", buildsystem="cmake",
            **{"sources": {"git": {
                "ref": "origin/master",
                "remotes": {"origin": {"url": "https://example.com/foo.git"}},
            }}})
        sol = self._write_solution(build=["sonicde/foo"])
        prj = Project(self.root, sol)
        prj.load()
        cl = prj.build_closure()
        model = prj.to_model(cl)
        for key in ["schema_version", "root", "solution_file",
                     "install_prefix", "source_root", "build_dir",
                     "state_dir", "packages", "edges", "topo_order",
                     "reverse", "platform", "skipped"]:
            self.assertIn(key, model, f"missing key: {key}")


class TestPlatforms(unittest.TestCase):
    """Packages restricted with `platforms` drop out of other platforms' closures."""

    setUp = TestRealSolutionStructure.setUp
    tearDown = TestRealSolutionStructure.tearDown
    _write_solution = TestRealSolutionStructure._write_solution
    _write_pkg = TestRealSolutionStructure._write_pkg

    def _git(self, name):
        return {"sources": {"git": {
            "ref": "origin/master",
            "remotes": {"origin": {"url": f"https://example.com/{name}.git"}},
        }}}

    def _app_with_linux_only_dep(self):
        self._write_pkg("sonicde/app", buildsystem="cmake",
                        depends=["sonicde/linuxonly", "sonicde/common"], **self._git("app"))
        self._write_pkg("sonicde/linuxonly", buildsystem="cmake", platforms=["linux"],
                        **self._git("linuxonly"))
        self._write_pkg("sonicde/common", buildsystem="cmake", **self._git("common"))
        return self._write_solution(build=["sonicde/app"])

    def _closure(self, sol, platform):
        prj = Project(self.root, sol, platform=platform)
        prj.load()
        return prj, prj.build_closure()

    def test_linux_only_dependency_skipped_on_freebsd(self):
        sol = self._app_with_linux_only_dep()
        prj, cl = self._closure(sol, "freebsd")
        self.assertNotIn("sonicde/linuxonly", cl.packages)
        self.assertIn("sonicde/common", cl.packages)
        self.assertFalse([e for e in cl.edges if e.prerequisite == "sonicde/linuxonly"])
        self.assertEqual(cl.skipped, {"sonicde/linuxonly": ["sonicde/app depends"]})
        model = prj.to_model(cl)
        self.assertEqual(model["platform"], "freebsd")
        self.assertIn("sonicde/linuxonly", model["skipped"])

    def test_linux_only_dependency_kept_on_linux(self):
        sol = self._app_with_linux_only_dep()
        _, cl = self._closure(sol, "linux")
        self.assertIn("sonicde/linuxonly", cl.packages)
        self.assertEqual(cl.skipped, {})

    def test_skipped_build_entry_is_not_an_error(self):
        self._write_pkg("sonicde/linuxonly", buildsystem="cmake", platforms=["linux"],
                        **self._git("linuxonly"))
        sol = self._write_solution(build=["sonicde/linuxonly"])
        _, cl = self._closure(sol, "freebsd")
        self.assertEqual(cl.packages, {})
        self.assertEqual(cl.skipped, {"sonicde/linuxonly": ["<solution::build>"]})

    def test_unknown_platform_value_rejected(self):
        self._write_pkg("sonicde/odd", buildsystem="cmake", platforms=["hurd"],
                        **self._git("odd"))
        sol = self._write_solution(build=["sonicde/odd"])
        with self.assertRaises(ConfigError):
            Project(self.root, sol, platform="linux").load()

    def test_unsupported_host_platform_rejected(self):
        sol = self._write_solution()
        with self.assertRaises(ConfigError):
            Project(self.root, sol, platform="darwin")

    def test_build_of_skipped_repository_exits_2(self):
        import contextlib
        import io
        from config import main
        sol = self._app_with_linux_only_dep()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            main(["-root", str(self.root), "-solution", str(sol), "-platform", "freebsd",
                  "-build", "linuxonly", "--closure-only"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("sonicde/linuxonly is not built on freebsd", err.getvalue())


class TestInstallDeps(unittest.TestCase):
    """install-deps picks os-release ID, then the platform's generic key."""

    MODEL = {"packages": {
        "os-installed/a": {"type": "system", "platform_packages": {
            "debian": "a-dev", "ghostbsd": "a-ghost", "freebsd": "a-fbsd"}},
        "os-installed/b": {"type": "system", "platform_packages": {
            "debian": "b-dev", "freebsd": "b-fbsd"}},
        "os-installed/c": {"type": "system", "platform_packages": {"debian": "c-dev"}},
        "os-installed/d": {"type": "system", "platform_packages": {
            "debian": "d-dev", "freebsd": "base"}},
        "sonicde/x": {"type": "", "platform_packages": {}},
    }}

    def test_freebsd_uses_os_id_then_freebsd_never_debian(self):
        from install_deps import select_packages
        names, missing = select_packages(dict(self.MODEL, platform="freebsd"), "ghostbsd")
        self.assertEqual(names, ["a-ghost", "b-fbsd"])
        self.assertEqual(missing, ["os-installed/c"])

    def test_linux_falls_back_to_debian(self):
        from install_deps import select_packages
        names, missing = select_packages(dict(self.MODEL, platform="linux"), "gentoo")
        self.assertEqual(names, ["a-dev", "b-dev", "c-dev", "d-dev"])
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
