#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for the version bump scripts.

Creates local git repos and explicit release lists, then verifies CMake
updates and commits (which never tag), and the separate --tag-only release
tagging of the accepted origin/master (including repositories without CMake).
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
        (self.root / "scripts").mkdir()
        for category in ("plasma", "frameworks", "system"):
            (self.root / "scripts" / f"version-repos-{category}.list").write_text("")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _list_repo(self, category: str, name: str):
        roster = self.root / "scripts" / f"version-repos-{category}.list"
        roster.write_text(roster.read_text() + name + "\n")

    def _make_repo(self, name: str, cmake_content: str | None,
                   category: str | None = None) -> Path:
        """Create a git repo under src/sonicde/<name> with a CMakeLists.txt.

        A bare ``origin`` remote is set up under ``origin/<name>.git`` so the
        bump script can push commits and tags.
        """
        repo = self.root / "src" / "sonicde" / name
        repo.mkdir(parents=True)
        # The scripts always work on master, whatever git's default branch is.
        git(repo, "init", "-b", "master")
        git(repo, "config", "user.email", "test@test.com")
        git(repo, "config", "user.name", "Test")
        if cmake_content is None:
            (repo / "asset.txt").write_text("release asset\n")
        else:
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

        # Existing test fixtures explicitly belong to these release lists.
        # This setup does not exercise or depend on production discovery.
        if category is None:
            category = {
                PLASMA_CMAKE: "plasma",
                FRAMEWORKS_CMAKE: "frameworks",
                FRAMEWORKS_CMAKE_NO_DEP: "frameworks",
                SYSTEM_CMAKE: "system",
                SYSTEM_CMAKE_SPACES: "system",
            }.get(cmake_content)
        if category:
            self._list_repo(category, name)
        return repo

    def _run_bump(self, category: str, version: str,
                  extra_args=None) -> subprocess.CompletedProcess:
        args = [sys.executable, str(VERSION_BUMP),
                category, version, "--root", str(self.root)]
        if extra_args:
            args.extend(extra_args)
        return subprocess.run(args, capture_output=True, text=True)

    def _run_tag(self, category: str, version: str,
                 extra_args=None) -> subprocess.CompletedProcess:
        return self._run_bump(category, version, ["--tag-only", *(extra_args or [])])

    def _origin(self, name: str) -> Path:
        return self.root / "origin" / f"{name}.git"

    def _tags(self, repo: Path) -> list[str]:
        return git(repo, "tag", "-l").stdout.split()

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

    def test_bump_updates_version_and_commits_without_tag(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        r = self._run_bump("plasma", "6.7.5")
        self.assertEqual(r.returncode, 0, r.stderr)

        content = (repo / "CMakeLists.txt").read_text()
        self.assertIn('set(PROJECT_VERSION "6.7.5")', content)
        self.assertNotIn('6.7.4', content)

        # Commit created
        log = git(repo, "log", "--oneline", "-1").stdout.strip()
        self.assertIn("Release 6.7.5", log)

        # Tagging is a separate --tag-only operation
        self.assertEqual(self._tags(repo), [])

        # No second version bump commit
        log_full = git(repo, "log", "--oneline").stdout.strip()
        lines = log_full.splitlines()
        self.assertEqual(len(lines), 2,
                         "expected initial + release commit only")

    def test_already_at_target_version_makes_no_commit(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        r = self._run_bump("plasma", "6.7.4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no CMake version change; no commit or tag", r.stdout)

        # No new commits
        log = git(repo, "log", "--oneline").stdout.strip()
        self.assertEqual(len(log.splitlines()), 1)
        self.assertEqual(self._tags(repo), [])

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

        self.assertEqual(self._tags(repo), [])

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

        self.assertEqual(self._tags(repo), [])

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
            self.assertIn("Release 6.7.5", git(repo, "log", "-1", "--format=%s").stdout)
        self.assertIn("processed 3 repo(s)", r.stdout)

        # Frameworks repo unchanged
        fwk_repo = self.root / "src" / "sonicde" / "sonic-fwk"
        fwk_content = (fwk_repo / "CMakeLists.txt").read_text()
        self.assertIn("6.29.0", fwk_content)
        self.assertEqual(len(git(fwk_repo, "log", "--oneline").stdout.splitlines()), 1)


class TestPush(VersionBumpTestBase):
    """Verify that a bump pushes only master, never tags."""

    def test_push_sends_master_without_tags(self):
        repo = self._make_repo("sonic-plasma", PLASMA_CMAKE)
        origin = self.root / "origin" / "sonic-plasma.git"
        git(repo, "tag", "local-only")

        r = self._run_bump("plasma", "6.7.5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pushed master to origin (no tags)", r.stdout)

        # No tag, including pre-existing local ones, reaches the bare origin
        self.assertEqual(self._tags(origin), [])

        # The release commit landed on origin's master
        origin_log = git(origin, "log", "--oneline", "-1", "master").stdout.strip()
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


class TestExplicitReleaseLists(VersionBumpTestBase):

    def test_unlisted_matching_cmake_is_not_discovered_or_modified(self):
        repo = self._make_repo("sonic-unlisted", PLASMA_CMAKE, category="")
        self.assertNotIn("sonic-unlisted", self._run_list("plasma").stdout)
        result = self._run_bump("plasma", "6.7.6")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((repo / "CMakeLists.txt").read_text(), PLASMA_CMAKE)
        self.assertEqual(git(repo, "tag", "-l").stdout.strip(), "")

    def test_membership_does_not_change_with_cmake_content(self):
        self._make_repo("sonic-listed", FRAMEWORKS_CMAKE, category="plasma")
        self.assertIn("sonic-listed", self._run_list("plasma").stdout)
        self.assertNotIn("sonic-listed", self._run_list("frameworks").stdout)

    def test_list_includes_repo_without_cmake_and_missing_checkout(self):
        self._make_repo("sonic-assets", None, category="plasma")
        self._list_repo("plasma", "sonic-not-fetched")
        result = self._run_list("plasma")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["sonic-assets", "sonic-not-fetched"])

    def test_missing_roster_fails_instead_of_discovering_repos(self):
        (self.root / "scripts" / "version-repos-plasma.list").unlink()
        result = self._run_list("plasma")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version-repos-plasma.list", result.stderr)

    def test_duplicate_and_unsafe_entries_are_rejected(self):
        for content in ("sonic-a\nsonic-a\n", "../sonic-a\n", "/tmp/repo\n"):
            with self.subTest(content=content):
                (self.root / "scripts" / "version-repos-plasma.list").write_text(content)
                self.assertNotEqual(self._run_list("plasma").returncode, 0)

    def test_missing_checkout_fails_without_touching_builder(self):
        self._list_repo("plasma", "sonic-missing")
        for run in (self._run_bump, self._run_tag):
            with self.subTest(operation=run.__name__):
                result = run("plasma", "6.7.6")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("repository checkout missing", result.stderr)
                self.assertIn("processed 0", result.stderr)
                self.assertFalse((self.root / ".git").exists())


class TestTagOnlyReleases(VersionBumpTestBase):

    def test_all_categories_tag_repos_without_cmake(self):
        for category, version in (("plasma", "6.7.6"), ("frameworks", "6.31.0"), ("system", "26.08.1")):
            with self.subTest(category=category):
                name = f"sonic-assets-{category}"
                repo = self._make_repo(name, None, category=category)
                initial = git(repo, "rev-parse", "HEAD").stdout.strip()
                result = self._run_tag(category, version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((repo / "CMakeLists.txt").exists())
                self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), initial)
                self.assertEqual(git(repo, "rev-parse", f"{version}^{{commit}}").stdout.strip(), initial)
                self.assertIn(version, self._tags(self._origin(name)))

    def test_versionless_cmake_is_unchanged_but_tagged(self):
        content = 'cmake_minimum_required(VERSION 3.16)\nproject(assets LANGUAGES NONE)\n'
        repo = self._make_repo("sonic-assets", content, category="plasma")
        initial = git(repo, "rev-parse", "HEAD").stdout.strip()
        bumped = self._run_bump("plasma", "6.7.6")
        self.assertEqual(bumped.returncode, 0, bumped.stderr)
        self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), initial)
        result = self._run_tag("plasma", "6.7.6")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((repo / "CMakeLists.txt").read_text(), content)
        self.assertEqual(git(repo, "rev-parse", "6.7.6^{commit}").stdout.strip(), initial)

    def test_current_cmake_without_tag_gets_tag_no_empty_commit(self):
        repo = self._make_repo("sonic-current", PLASMA_CMAKE)
        initial = git(repo, "rev-parse", "HEAD").stdout.strip()
        result = self._run_tag("plasma", "6.7.4")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), initial)
        self.assertEqual(git(repo, "rev-parse", "6.7.4^{commit}").stdout.strip(), initial)

    def test_bump_then_tag_tags_the_release_commit(self):
        repo = self._make_repo("sonic-release", PLASMA_CMAKE)
        self.assertEqual(self._run_bump("plasma", "6.7.5").returncode, 0)
        release = git(repo, "rev-parse", "HEAD").stdout.strip()
        result = self._run_tag("plasma", "6.7.5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(repo, "rev-parse", "6.7.5^{commit}").stdout.strip(), release)
        self.assertEqual(git(self._origin("sonic-release"), "rev-parse", "6.7.5^{commit}").stdout.strip(), release)
        self.assertEqual(len(git(repo, "log", "--oneline").stdout.splitlines()), 2)

    def test_unbumped_cmake_is_not_tagged(self):
        repo = self._make_repo("sonic-stale", PLASMA_CMAKE)
        result = self._run_tag("plasma", "6.7.5")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("run the regular version-bump command first", result.stderr)
        self.assertEqual(self._tags(repo), [])
        self.assertEqual((repo / "CMakeLists.txt").read_text(), PLASMA_CMAKE)

    def test_existing_tag_at_origin_master_is_reused(self):
        repo = self._make_repo("sonic-tagged", PLASMA_CMAKE)
        git(repo, "tag", "6.7.4")
        initial = git(repo, "rev-parse", "HEAD").stdout.strip()
        result = self._run_tag("plasma", "6.7.4")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(repo, "rev-parse", "6.7.4^{commit}").stdout.strip(), initial)
        self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), initial)
        self.assertIn("6.7.4", self._tags(self._origin("sonic-tagged")))

    def test_existing_tag_elsewhere_never_moves(self):
        repo = self._make_repo("sonic-moved", PLASMA_CMAKE)
        git(repo, "tag", "6.7.4")
        tagged = git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "notes.txt").write_text("later change\n")
        git(repo, "add", "notes.txt")
        git(repo, "commit", "-m", "later change")
        git(repo, "push", "origin", "HEAD:refs/heads/master")
        result = self._run_tag("plasma", "6.7.4")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("already points elsewhere", result.stderr)
        self.assertEqual(git(repo, "rev-parse", "6.7.4^{commit}").stdout.strip(), tagged)
        self.assertEqual(self._tags(self._origin("sonic-moved")), [])

    def test_dirty_repo_fails_without_tag(self):
        repo = self._make_repo("sonic-dirty", None, category="plasma")
        (repo / "asset.txt").write_text("local changes\n")
        result = self._run_tag("plasma", "6.7.6")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("working tree is not clean", result.stderr)
        self.assertEqual(self._tags(repo), [])

    def test_tag_only_dry_run_does_not_modify_or_push(self):
        repo = self._make_repo("sonic-assets", None, category="plasma")
        result = self._run_tag("plasma", "6.7.6", ["--dry-run"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would create tag 6.7.6", result.stdout)
        self.assertEqual(self._tags(repo), [])
        self.assertEqual(self._tags(self._origin("sonic-assets")), [])

    def test_tag_only_no_push_leaves_origin_untouched(self):
        repo = self._make_repo("sonic-assets", None, category="plasma")
        result = self._run_tag("plasma", "6.7.6", ["--no-push"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("6.7.6", self._tags(repo))
        self.assertEqual(self._tags(self._origin("sonic-assets")), [])

    def test_pending_release_notes_are_published_and_tagged(self):
        repo = self._make_repo("sonic-notes", None, category="plasma")
        notes = "# Sonic Notes Release Notes\n\n## 6.7.6 (2026-11-01)\n\n- Better things.\n"
        (repo / "RELEASE-NOTES.md").write_text(notes)
        (repo / "scratch.txt").write_text("unrelated local file\n")
        result = self._run_tag("plasma", "6.7.6")
        self.assertEqual(result.returncode, 0, result.stderr)
        # The tag is on a commit that carries exactly the notes, and origin has both.
        self.assertEqual(git(repo, "show", "6.7.6:RELEASE-NOTES.md").stdout, notes)
        self.assertEqual(git(repo, "show", "--name-only", "--format=", "6.7.6").stdout.split(), ["RELEASE-NOTES.md"])
        origin = self._origin("sonic-notes")
        self.assertEqual(git(origin, "rev-parse", "6.7.6^{commit}").stdout,
                         git(origin, "rev-parse", "master").stdout)
        self.assertIn("scratch.txt", git(repo, "status", "--porcelain").stdout)

    def test_release_notes_without_a_section_for_the_version_are_not_tagged(self):
        for heading in ("## Unreleased", "## 6.7.6.1 (2026-11-02)"):
            with self.subTest(heading=heading):
                name = f"sonic-notes-{len(heading)}"
                repo = self._make_repo(name, None, category="plasma")
                (repo / "RELEASE-NOTES.md").write_text(f"# Notes\n\n{heading}\n\n- Change.\n")
                git(repo, "add", "RELEASE-NOTES.md")
                git(repo, "commit", "-m", "notes")
                git(repo, "push", "origin", "HEAD:refs/heads/master")
                result = self._run_tag("plasma", "6.7.6")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("has no section for 6.7.6", result.stderr)
                self.assertNotIn("6.7.6", self._tags(repo))
                self.assertEqual(self._tags(self._origin(name)), [])
                # Each case lists only its own repository.
                (self.root / "scripts" / "version-repos-plasma.list").write_text("")

    def test_pending_release_notes_are_not_tagged_without_push(self):
        repo = self._make_repo("sonic-notes", None, category="plasma")
        (repo / "RELEASE-NOTES.md").write_text("## 6.7.6 (2026-11-01)\n\n- Change.\n")
        result = self._run_tag("plasma", "6.7.6", ["--no-push"])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unpublished changes", result.stderr)
        self.assertEqual(self._tags(repo), [])
        self.assertEqual(len(git(repo, "log", "--oneline").stdout.splitlines()), 1)

    def test_dry_run_reports_pending_release_notes_without_committing(self):
        repo = self._make_repo("sonic-notes", None, category="plasma")
        (repo / "RELEASE-NOTES.md").write_text("## 6.7.6 (2026-11-01)\n\n- Change.\n")
        result = self._run_tag("plasma", "6.7.6", ["--dry-run"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would commit and push RELEASE-NOTES.md", result.stdout)
        self.assertEqual(len(git(repo, "log", "--oneline").stdout.splitlines()), 1)
        self.assertEqual(self._tags(repo), [])

    def test_independent_system_library_version_is_not_changed(self):
        content = 'cmake_minimum_required(VERSION 3.16)\nproject(libkexiv2 VERSION "5.1.0")\n'
        repo = self._make_repo("sonic-exiv2-library", content, category="system")
        bumped = self._run_bump("system", "26.08.1")
        self.assertEqual(bumped.returncode, 0, bumped.stderr)
        self.assertEqual((repo / "CMakeLists.txt").read_text(), content)
        self.assertEqual(len(git(repo, "log", "--oneline").stdout.splitlines()), 1)
        result = self._run_tag("system", "26.08.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("26.08.1", self._tags(repo))


class TestExistingAlternativeVersionFields(VersionBumpTestBase):

    def test_ecm_version_field_is_updated(self):
        content = 'set(VERSION "6.30.0")\nproject(ECM VERSION "${VERSION}" LANGUAGES NONE)\n'
        repo = self._make_repo("sonic-frameworks-cmake-modules", content, category="frameworks")
        result = self._run_bump("frameworks", "6.31.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('set(VERSION "6.31.0")', (repo / "CMakeLists.txt").read_text())
        self.assertEqual(self._tags(repo), [])
        # The ECM-specific field also gates tagging.
        tagged = self._run_tag("frameworks", "6.31.0")
        self.assertEqual(tagged.returncode, 0, tagged.stderr)
        self.assertIn("6.31.0", self._tags(repo))

    def test_literal_plasma_project_version_is_updated(self):
        for declaration in ('project(breeze VERSION 6.7.5 LANGUAGES CXX)', 'project(breeze VERSION "6.7.5" LANGUAGES CXX)'):
            with self.subTest(declaration=declaration):
                name = "sonic-breeze-quoted" if '"' in declaration else "sonic-breeze-unquoted"
                content = 'cmake_minimum_required(VERSION 3.16)\n' + declaration + '\nset(KF6_MIN_VERSION "6.26.0")\n'
                repo = self._make_repo(name, content, category="plasma")
                result = self._run_bump("plasma", "6.7.6")
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = content.replace("6.7.5", "6.7.6")
                self.assertEqual((repo / "CMakeLists.txt").read_text(), expected)
                self.assertEqual(self._tags(repo), [])

    def test_failed_cmake_commit_fails_without_push(self):
        repo = self._make_repo("sonic-failed", PLASMA_CMAKE)
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        result = self._run_bump("plasma", "6.7.5")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("commit failed", result.stderr)
        self.assertEqual(self._tags(repo), [])
        origin = self.root / "origin" / "sonic-failed.git"
        self.assertEqual(git(origin, "log", "-1", "--format=%s", "master").stdout.strip(), "initial")


class TestMaintainedLists(unittest.TestCase):

    def test_lists_are_sorted_disjoint_and_include_known_exceptions(self):
        lists = {}
        for category in ("plasma", "frameworks", "system"):
            lines = (SCRIPTS / f"version-repos-{category}.list").read_text().splitlines()
            repos = [line for line in lines if line and not line.startswith("#")]
            self.assertEqual(repos, sorted(set(repos)))
            lists[category] = set(repos)
        self.assertFalse(lists["plasma"] & lists["frameworks"])
        self.assertFalse(lists["plasma"] & lists["system"])
        self.assertFalse(lists["frameworks"] & lists["system"])
        self.assertIn("sonic-breeze-sddm", lists["plasma"])
        self.assertIn("sonic-thunderbolt", lists["plasma"])
        self.assertIn("sonic-frameworks-cmake-modules", lists["frameworks"])
        self.assertIn("sonic-exiv2-library", lists["system"])
        all_repos = set().union(*lists.values())
        for independent in ("sonic-crypto-library", "sonic-frameworks-io-fuse", "sonic-frameworks-quick-ui-addons", "sonic-frameworks-sso",
                            "sonic-polkit", "sonic-pulseaudio-library", "sonic-pyside", "sonic-qt-accounts-library", "sonic-quick-image-editor",
                            "sonic-silver-sddm", "sonic-l10n-templates"):
            self.assertNotIn(independent, all_repos)


if __name__ == "__main__":
    unittest.main()
