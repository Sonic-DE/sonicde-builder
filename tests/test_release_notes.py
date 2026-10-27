#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for release_notes.py: which changes count as SonicDE's own since the
last SonicDE release, using local repositories with origin and upstream remotes."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_notes.py"


def git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"git {args} in {cwd}: {result.stderr}")
    return result.stdout.strip()


def commit(repo: Path, name: str, subject: str) -> str:
    (repo / name).write_text(subject + "\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "--short", "HEAD")


class ReleaseNotesTestBase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        (self.root / "scripts").mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _repo(self, name: str, with_upstream: bool) -> tuple[Path, Path | None]:
        repo = self.root / "src" / "sonicde" / name
        repo.mkdir(parents=True)
        git(repo, "init", "-q", "-b", "master")
        git(repo, "config", "user.email", "test@test.com")
        git(repo, "config", "user.name", "Test")
        commit(repo, "base.txt", "shared base")
        upstream = None
        if with_upstream:
            upstream = self.root / "upstream" / name
            git(self.root, "clone", "-q", str(repo), str(upstream))
            git(upstream, "config", "user.email", "up@test.com")
            git(upstream, "config", "user.name", "Upstream")
            git(repo, "remote", "add", "upstream", str(upstream))
        return repo, upstream

    def _fetch_upstream(self, repo: Path):
        git(repo, "fetch", "-q", "--no-tags", "upstream", "+refs/heads/*:refs/remotes/upstream/*")
        git(repo, "remote", "set-head", "upstream", "master")

    def _run(self, *args: str) -> list[list[str]]:
        result = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line.split("\t") for line in result.stdout.splitlines()[1:]]


class TestSonicChanges(ReleaseNotesTestBase):

    def test_only_sonic_changes_since_the_last_sonic_release_are_listed(self):
        repo, upstream = self._repo("sonic-foo", with_upstream=True)
        commit(repo, "ours1.txt", "sonic change before release")
        git(repo, "tag", "6.8.0")
        # An upstream change arrives later as a rebased (patch-equivalent) commit.
        commit(upstream, "up.txt", "upstream fix")
        self._fetch_upstream(repo)
        git(repo, "cherry-pick", "upstream/master")
        ours = commit(repo, "ours2.txt", "sonic change after release")
        # Upstream's "v" tags never count as SonicDE releases.
        git(repo, "tag", "v6.9.0")

        rows = self._run("--repo", "sonic-foo")
        self.assertEqual([(r[1], r[2], r[5]) for r in rows], [("6.8.0", ours, "sonic change after release")])

    def test_upstream_backport_with_a_changed_patch_is_excluded_by_subject(self):
        repo, upstream = self._repo("sonic-foo", with_upstream=True)
        commit(repo, "ours.txt", "sonic release work")
        git(repo, "tag", "6.8.0")
        commit(upstream, "up.txt", "upstream fix")
        self._fetch_upstream(repo)
        commit(repo, "up.txt", "upstream fix")  # same subject, different content
        (repo / "up.txt").write_text("conflict resolved differently\n")
        git(repo, "commit", "-q", "-am", "sonic follow-up")

        rows = self._run("--repo", "sonic-foo")
        self.assertEqual([r[5] for r in rows], ["sonic follow-up"])

    def test_unprefixed_tag_on_an_upstream_branch_is_not_a_sonic_release(self):
        repo, upstream = self._repo("sonic-foo", with_upstream=True)
        commit(repo, "release.txt", "sonic release work")
        git(repo, "tag", "6.8.0")
        git(upstream, "tag", "8.61")
        commit(upstream, "up.txt", "upstream release work")
        git(upstream, "tag", "8.62")
        self._fetch_upstream(repo)
        git(repo, "fetch", "-q", "upstream", "refs/tags/8.62:refs/tags/8.62")
        git(repo, "merge", "-q", "--no-edit", "upstream/master")
        commit(repo, "ours.txt", "sonic change")

        rows = self._run("--repo", "sonic-foo")
        self.assertEqual({r[1] for r in rows}, {"6.8.0"})
        self.assertEqual([r[5] for r in rows], ["sonic change"])

    def test_repository_without_upstream_lists_every_commit_since_its_release(self):
        repo, _ = self._repo("sonic-own", with_upstream=False)
        git(repo, "tag", "1.0.0")
        commit(repo, "a.txt", "first change")
        commit(repo, "b.txt", "second change")
        rows = self._run("--repo", "sonic-own")
        self.assertEqual([(r[1], r[5]) for r in rows], [("1.0.0", "first change"), ("1.0.0", "second change")])

    def test_repository_with_no_changes_gets_one_placeholder_row(self):
        repo, _ = self._repo("sonic-quiet", with_upstream=False)
        git(repo, "tag", "6.8.0")
        rows = self._run("--repo", "sonic-quiet")
        self.assertEqual(rows, [["sonic-quiet", "6.8.0", "-", "-", "-", "-"]])

    def test_category_reads_the_maintained_release_list(self):
        for name in ("sonic-b", "sonic-a"):
            repo, _ = self._repo(name, with_upstream=False)
            git(repo, "tag", "6.8.0")
        (self.root / "scripts" / "version-repos-plasma.list").write_text("# core\nsonic-b\nsonic-a\n")
        rows = self._run("core")
        self.assertEqual([r[0] for r in rows], ["sonic-a", "sonic-b"])


if __name__ == "__main__":
    unittest.main()
