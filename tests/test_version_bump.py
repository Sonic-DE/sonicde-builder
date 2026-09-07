#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the version bump scripts.

Creates local git repos with CMakeLists.txt files matching each category's
version pattern, then verifies the bump, commit, and tag behavior.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
VERSION_BUMP = SCRIPTS / "version_bump.py"


def git(cwd, *args, check=True):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} in {cwd}: {r.stderr}")
    return r


class VersionBumpTestBase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_repo(self, name: str, cmake_content: str) -> Path:
        """Create a git repo under src/sonicde/<name> with a CMakeLists.txt.

        A bare ``origin`` remote is set up under ``origin/<name>.git`` so the
        bump script can push commits and tags.
        """
        repo = self.root / "src" / "sonicde" / name
        repo.mkdir(parents=True)
        git(repo, "init")
        git(repo, "config", "user.email", "test@test.com")
        git(repo, "config", "user.name", "Test")
        (repo / "CMakeLists.txt").write_text(cmake_content)
        git(repo, "add", ".")
        git(repo, "commit", "-m", "initial")

        # Bare origin remote
        origin = self.root / "origin" / f"{name}.git"
        origin.mkdir(parents=True)
        git(origin, "init", "--bare")
        git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "origin", "HEAD")
        branch = git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
        git(repo, "config", f"branch.{branch}.remote", "origin")
        git(repo, "config", f"branch.{branch}.merge", f"refs/heads/{branch}")
        return repo

    def _run_bump(self, category: str, version: str,
                  extra_args=None) -> subprocess.CompletedProcess:
        args = [sys.executable, str(VERSION_BUMP),
                category, version, "--root", str(self.root)]
        if extra_args:
            args.extend(extra_args)
        return subprocess.run(args, capture_output=True, text=True)

    def _run_list(self, category: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(VERSION_BUMP),
             category, "--list", "--root", str(self.root)],
            capture_output=True, text=True)


PLASMA_CMAKE = '''cmake_minimum_required(VERSION 3.16)
set(PROJECT_VERSION "6.7.4") # Handled by release scripts
project(MyProject VERSION ${PROJECT_VERSION})
'''

FRAMEWORKS_CMAKE = '''cmake_minimum_required(VERSION 3.29)
set(KF_VERSION "6.29.0") # handled by release scripts
set(KF_DEP_VERSION "6.28.0") # handled by release scripts
project(KFoo VERSION ${KF_VERSION})
find_package(ECM 6.28.0 REQUIRED NO_MODULE)
'''

FRAMEWORKS_CMAKE_NO_DEP = '''cmake_minimum_required(VERSION 3.29)
set(KF_VERSION "6.29.0") # handled by release scripts
project(KFoo VERSION ${KF_VERSION})
find_package(ECM 6.28.0  NO_MODULE)
'''

SYSTEM_CMAKE = '''cmake_minimum_required(VERSION 3.16)
set(RELEASE_SERVICE_VERSION_MAJOR "26")
set(RELEASE_SERVICE_VERSION_MINOR "04")
set(RELEASE_SERVICE_VERSION_MICRO "3")
set(RELEASE_SERVICE_VERSION "${RELEASE_SERVICE_VERSION_MAJOR}.${RELEASE_SERVICE_VERSION_MINOR}.${RELEASE_SERVICE_VERSION_MICRO}")
project(MyApp VERSION ${RELEASE_SERVICE_VERSION})
'''

SYSTEM_CMAKE_SPACES = '''cmake_minimum_required(VERSION 3.16)
set (RELEASE_SERVICE_VERSION_MAJOR "26")
set (RELEASE_SERVICE_VERSION_MINOR "04")
set (RELEASE_SERVICE_VERSION_MICRO "3")
set (RELEASE_SERVICE_VERSION "${RELEASE_SERVICE_VERSION_MAJOR}.${RELEASE_SERVICE_VERSION_MINOR}.${RELEASE_SERVICE_VERSION_MICRO}")
project(MyApp VERSION ${RELEASE_SERVICE_VERSION})
'''

# Non-matching repos use their own version variables:
NONCATEGORY_CMAKE = '''cmake_minimum_required(VERSION 3.16)
set(QCA_LIB_MAJOR_VERSION "2")
set(QCA_LIB_MINOR_VERSION "3")
project(qca)
'''


class TestListRepos(VersionBumpTestBase):

    def test_plasma_list(self):
        self._make_repo("sonic-plasma-a", PLASMA_CMAKE)
        self._make_repo("sonic-plasma-b", PLASMA_CMAKE)
        self._make_repo("sonic-fwk", FRAMEWORKS_CMAKE)
        r = self._run_list("plasma")
        self.assertEqual(r.returncode, 0, r.stderr)
        repos = r.stdout.strip().splitlines()
        self.assertIn("sonic-plasma-a", repos)
        self.assertIn("sonic-plasma-b", repos)
        self.assertNotIn("sonic-fwk", repos)

    def test_frameworks_list(self):
        self._make_repo("sonic-fwk-a", FRAMEWORKS_CMAKE)
        self._make_repo("sonic-plasma", PLASMA_CMAKE)
        r = self._run_list("frameworks")
        self.assertEqual(r.returncode, 0, r.stderr)
        repos = r.stdout.strip().splitlines()
        self.assertIn("sonic-fwk-a", repos)
        self.assertNotIn("sonic-plasma", repos)

    def test_system_list_includes_spaces_pattern(self):
        self._make_repo("sonic-sys-a", SYSTEM_CMAKE)
        self._make_repo("sonic-sys-b", SYSTEM_CMAKE_SPACES)
        self._make_repo("sonic-plasma", PLASMA_CMAKE)
        r = self._run_list("system")
        self.assertEqual(r.returncode, 0, r.stderr)
        repos = r.stdout.strip().splitlines()
        self.assertIn("sonic-sys-a", repos)
        self.assertIn("sonic-sys-b", repos)
        self.assertNotIn("sonic-plasma", repos)

    def test_noncategory_repos_excluded(self):
        self._make_repo("sonic-other", NONCATEGORY_CMAKE)
        r_plasma = self._run_list("plasma")
        r_frameworks = self._run_list("frameworks")
        r_system = self._run_list("system")
        for r in (r_plasma, r_frameworks, r_system):
            self.assertNotIn("sonic-other", r.stdout)


class TestPlasmaBump(VersionBumpTestBase):

    def test_bump_updates_version_and_creates_tag(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        r = self._run_bump("plasma", "6.7.5")
        self.assertEqual(r.returncode, 0, r.stderr)

        content = (repo / "CMakeLists.txt").read_text()
        self.assertIn('set(PROJECT_VERSION "6.7.5")', content)
        self.assertNotIn('6.7.4', content)

        # Commit created
        log = git(repo, "log", "--oneline", "-1").stdout.strip()
        self.assertIn("Release 6.7.5", log)

        # Tag created
        tags = git(repo, "tag", "-l").stdout.strip()
        self.assertIn("6.7.5", tags)

        # No second version bump commit
        log_full = git(repo, "log", "--oneline").stdout.strip()
        lines = log_full.splitlines()
        self.assertEqual(len(lines), 2,
                         "expected initial + release commit only")

    def test_skip_already_at_target_version(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        r = self._run_bump("plasma", "6.7.4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already at", r.stdout)

        # No new commits
        log = git(repo, "log", "--oneline").stdout.strip()
        self.assertEqual(len(log.splitlines()), 1)

    def test_dry_run_does_not_modify(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        r = self._run_bump("plasma", "6.7.5", ["--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would bump", r.stdout)

        content = (repo / "CMakeLists.txt").read_text()
        self.assertIn('6.7.4', content)

        log = git(repo, "log", "--oneline").stdout.strip()
        self.assertEqual(len(log.splitlines()), 1)


class TestFrameworksBump(VersionBumpTestBase):

    def test_bump_updates_all_three_locations(self):
        """KF_VERSION, KF_DEP_VERSION, and find_package(ECM) all get updated."""
        repo = self._make_repo("sonic-fwk", FRAMEWORKS_CMAKE)
        r = self._run_bump("frameworks", "6.30.0")
        self.assertEqual(r.returncode, 0, r.stderr)

        content = (repo / "CMakeLists.txt").read_text()
        self.assertIn('set(KF_VERSION "6.30.0")', content)
        self.assertIn('set(KF_DEP_VERSION "6.30.0")', content)
        self.assertIn('find_package(ECM 6.30.0', content)
        self.assertNotIn("6.29.0", content)
        self.assertNotIn("6.28.0", content)

        tags = git(repo, "tag", "-l").stdout.strip()
        self.assertIn("6.30.0", tags)

        log = git(repo, "log", "--oneline").stdout.strip()
        self.assertEqual(len(log.splitlines()), 2)

    def test_bump_without_kf_dep_version(self):
        """Repos without KF_DEP_VERSION still work."""
        repo = self._make_repo("sonic-fwk-nodep", FRAMEWORKS_CMAKE_NO_DEP)
        r = self._run_bump("frameworks", "6.30.0")
        self.assertEqual(r.returncode, 0, r.stderr)

        content = (repo / "CMakeLists.txt").read_text()
        self.assertIn('set(KF_VERSION "6.30.0")', content)
        self.assertIn('find_package(ECM 6.30.0', content)
        self.assertNotIn("6.29.0", content)
        self.assertNotIn("6.28.0", content)


class TestSystemBump(VersionBumpTestBase):

    def test_bump_updates_all_three_components(self):
        repo = self._make_repo("sonic-sys", SYSTEM_CMAKE)
        r = self._run_bump("system", "26.04.4")
        self.assertEqual(r.returncode, 0, r.stderr)

        content = (repo / "CMakeLists.txt").read_text()
        self.assertIn('set(RELEASE_SERVICE_VERSION_MAJOR "26")', content)
        self.assertIn('set(RELEASE_SERVICE_VERSION_MINOR "04")', content)
        self.assertIn('set(RELEASE_SERVICE_VERSION_MICRO "4")', content)

        tags = git(repo, "tag", "-l").stdout.strip()
        self.assertIn("26.04.4", tags)

    def test_bump_handles_spaces_in_set(self):
        repo = self._make_repo("sonic-sys-spaces", SYSTEM_CMAKE_SPACES)
        r = self._run_bump("system", "26.04.4")
        self.assertEqual(r.returncode, 0, r.stderr)

        content = (repo / "CMakeLists.txt").read_text()
        self.assertIn('"26"', content)
        self.assertIn('"04"', content)
        self.assertIn('"4"', content)
        self.assertNotIn('"3"', content)

    def test_bump_major_version_change(self):
        repo = self._make_repo("sonic-sys", SYSTEM_CMAKE)
        r = self._run_bump("system", "26.05.0")
        self.assertEqual(r.returncode, 0, r.stderr)

        content = (repo / "CMakeLists.txt").read_text()
        self.assertIn('set(RELEASE_SERVICE_VERSION_MINOR "05")', content)
        self.assertIn('set(RELEASE_SERVICE_VERSION_MICRO "0")', content)


class TestNoSecondBump(VersionBumpTestBase):
    """Verify that no second 'bump to next dev version' commit is created."""

    def test_plasma_no_second_commit(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        self._run_bump("plasma", "6.7.5")

        log = git(repo, "log", "--format=%s").stdout.strip().splitlines()
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0], "Release 6.7.5")
        self.assertEqual(log[1], "initial")

    def test_frameworks_no_second_commit(self):
        repo = self._make_repo("sonic-fwk", FRAMEWORKS_CMAKE)
        self._run_bump("frameworks", "6.30.0")

        log = git(repo, "log", "--format=%s").stdout.strip().splitlines()
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0], "Release 6.30.0")
        self.assertEqual(log[1], "initial")

    def test_system_no_second_commit(self):
        repo = self._make_repo("sonic-sys", SYSTEM_CMAKE)
        self._run_bump("system", "26.04.4")

        log = git(repo, "log", "--format=%s").stdout.strip().splitlines()
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0], "Release 26.04.4")
        self.assertEqual(log[1], "initial")


class TestMultipleRepos(VersionBumpTestBase):

    def test_bump_all_plasma_repos(self):
        repos = []
        for i in range(3):
            name = f"sonic-plasma-{i}"
            repos.append(self._make_repo(name, PLASMA_CMAKE))
        # Add a non-matching repo
        self._make_repo("sonic-fwk", FRAMEWORKS_CMAKE)

        r = self._run_bump("plasma", "6.7.5")
        self.assertEqual(r.returncode, 0, r.stderr)

        for repo in repos:
            content = (repo / "CMakeLists.txt").read_text()
            self.assertIn('set(PROJECT_VERSION "6.7.5")', content)
            tags = git(repo, "tag", "-l").stdout.strip()
            self.assertIn("6.7.5", tags)

        # Frameworks repo unchanged
        fwk_repo = self.root / "src" / "sonicde" / "sonic-fwk"
        fwk_content = (fwk_repo / "CMakeLists.txt").read_text()
        self.assertIn("6.29.0", fwk_content)
        fwk_tags = git(fwk_repo, "tag", "-l").stdout.strip()
        self.assertNotIn("6.7.5", fwk_tags)


class TestPush(VersionBumpTestBase):
    """Verify that commits and tags are pushed to origin."""

    def test_push_commit_and_tag_to_origin(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        origin = self.root / "origin" / "sonic-plasma.git"

        r = self._run_bump("plasma", "6.7.5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pushed 6.7.5 to origin", r.stdout)

        # Tag exists on the bare origin
        origin_tags = git(origin, "tag", "-l").stdout.strip()
        self.assertIn("6.7.5", origin_tags)

        # The release commit landed on origin's HEAD
        origin_log = git(origin, "log", "--oneline", "-1").stdout.strip()
        self.assertIn("Release 6.7.5", origin_log)

    def test_no_push_skips_remote(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        origin = self.root / "origin" / "sonic-plasma.git"

        r = self._run_bump("plasma", "6.7.5", ["--no-push"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("pushed", r.stdout)

        # Origin untouched
        self.assertEqual(git(origin, "tag", "-l").stdout.strip(), "")
        origin_log = git(origin, "log", "--oneline", "-1").stdout.strip()
        self.assertIn("initial", origin_log)

    def test_dry_run_does_not_push(self):
        self._make_repo("sonic-plasma", PLASMA_CMAKE)
        origin = self.root / "origin" / "sonic-plasma.git"

        r = self._run_bump("plasma", "6.7.5", ["--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would bump", r.stdout)

        self.assertEqual(git(origin, "tag", "-l").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
