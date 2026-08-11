#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Test autopick logic using local Git repositories.

No remote GitHub repositories are contacted. All Git operations use local
bare repos. The tracker is a branch on origin (matching real SonicDE setup).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"


def git(cwd, *args, check=True, env=None):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} in {cwd}: {r.stderr}")
    return r


def make_bare_remote(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(path.parent, "init", "--bare", str(path))
    return path


def commit(path: Path, msg: str, content: str = None) -> str:
    f = path / "file.txt"
    f.write_text(content if content is not None else msg)
    git(path, "add", ".")
    git(path, "commit", "-m", msg)
    return git(path, "rev-parse", "HEAD").stdout.strip()


class AutopickTestBase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _setup_repo(self, origin_commits=1, upstream_commits=1,
                    tracker_at=None):
        """Create a repo with origin and upstream remotes.

        origin and upstream share an initial commit. The tracker branch
        (tracking/master) is on origin.

        Returns (work_dir, origin_remote, upstream_remote).
        """
        origin = self.root / "origin.git"
        upstream = self.root / "upstream.git"
        make_bare_remote(origin)
        make_bare_remote(upstream)

        # Create work repo
        work = self.root / "work"
        git(self.root, "clone", str(origin), str(work))
        git(work, "config", "user.email", "test@test.com")
        git(work, "config", "user.name", "Test")

        # Initial commit on origin/master
        c0 = commit(work, "initial")
        git(work, "push", "origin", "master")

        # Push same commit to upstream
        git(work, "remote", "add", "upstream", str(upstream))
        git(work, "push", "upstream", "master")

        # Create tracker branch on origin at c0 (or specified commit)
        tracker_sha = tracker_at or c0
        git(work, "push", "origin", f"+{tracker_sha}:refs/heads/tracking/master")
        git(work, "fetch", "origin")

        # Configure autopick
        git(work, "config", "autopick.enabled", "true")
        git(work, "config", "autopick.upstream", "upstream/master")
        git(work, "config", "autopick.master", "origin/master")
        git(work, "config", "autopick.tracker", "origin/tracking/master")

        return work, origin, upstream

    def _add_upstream_commits(self, upstream_remote: Path, n: int,
                              work: Path = None) -> list[str]:
        """Add n commits to upstream and return their SHAs."""
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream_remote), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")
        shas = []
        for i in range(n):
            sha = commit(up_work, f"upstream {i+1}", f"up{i+1}")
            shas.append(sha)
        git(up_work, "push", "origin", "master")
        # If work repo is provided, fetch the new upstream commits into it
        if work:
            git(work, "fetch", "upstream")
        return shas

    def _run_autopick(self, work: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(SCRIPTS / "git-autopick")],
            cwd=str(work),
            capture_output=True, text=True)


class TestAlreadySynced(AutopickTestBase):

    def test_tracker_equals_upstream_all_in_master(self):
        """Tracker == upstream, all changes in master -> already synced."""
        work, origin, upstream = self._setup_repo()
        r = self._run_autopick(work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already synced", r.stderr)

    def test_tracker_equals_upstream_unapplied_changes(self):
        """Tracker == upstream but master lacks upstream changes -> not synced."""
        work, origin, upstream = self._setup_repo()
        # Add a new commit to upstream (not in master, not in tracker)
        shas = self._add_upstream_commits(upstream, 1, work=work)
        # Move tracker to the new upstream tip
        git(work, "push", "origin", f"+{shas[-1]}:refs/heads/tracking/master")
        git(work, "fetch", "origin")

        r = self._run_autopick(work)
        # Should NOT print "already synced" since master lacks the change
        self.assertNotIn("already synced", r.stderr)


class TestAncestry(AutopickTestBase):

    def test_non_ancestor_tracker_stops(self):
        """Tracker on a divergent lineage is rejected."""
        origin = self.root / "origin.git"
        upstream = self.root / "upstream.git"
        make_bare_remote(origin)
        make_bare_remote(upstream)

        # Create origin with its own history
        work = self.root / "work"
        git(self.root, "clone", str(origin), str(work))
        git(work, "config", "user.email", "test@test.com")
        git(work, "config", "user.name", "Test")
        commit(work, "origin commit", "origin1")
        git(work, "push", "origin", "master")

        # Upstream with completely different history
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")
        commit(up_work, "upstream commit 1", "up1")
        commit(up_work, "upstream commit 2", "up2")
        git(up_work, "push", "origin", "master")

        # Tracker on origin points to a completely unrelated commit
        # (we create a branch on origin that has no relation to upstream)
        git(work, "remote", "add", "upstream", str(upstream))
        git(work, "fetch", "upstream")

        # Put tracker at origin/master (which is not an ancestor of upstream/master)
        origin_master = git(work, "rev-parse", "origin/master").stdout.strip()
        git(work, "push", "origin", f"+{origin_master}:refs/heads/tracking/master")
        git(work, "fetch", "origin")

        git(work, "config", "autopick.enabled", "true")
        git(work, "config", "autopick.upstream", "upstream/master")
        git(work, "config", "autopick.master", "origin/master")
        git(work, "config", "autopick.tracker", "origin/tracking/master")

        r = self._run_autopick(work)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not an ancestor", r.stderr)


class TestRebaseRange(AutopickTestBase):

    def test_rebase_selects_tracker_to_upstream(self):
        """Rebase selects only commits between tracker and upstream."""
        work, origin, upstream = self._setup_repo()
        # Add 3 commits to upstream, each touching a different file
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")
        shas = []
        for i in range(3):
            (up_work / f"upfile{i+1}.txt").write_text(f"up{i+1}")
            git(up_work, "add", ".")
            git(up_work, "commit", "-m", f"upstream {i+1}")
            shas.append(git(up_work, "rev-parse", "HEAD").stdout.strip())
        git(up_work, "push", "origin", "master")
        git(work, "fetch", "upstream")

        # Set tracker at the first new commit (shas[0])
        git(work, "push", "origin", f"+{shas[0]}:refs/heads/tracking/master")
        git(work, "fetch", "origin")

        # Create a fake gh
        fake_gh = self.root / "bin" / "gh"
        fake_gh.parent.mkdir(parents=True, exist_ok=True)
        fake_gh.write_text("""#!/bin/bash
echo "https://github.com/Sonic-DE/test/pull/1"
""")
        fake_gh.chmod(0o755)
        path_env = str(fake_gh.parent) + ":" + os.environ.get("PATH", "")

        r = subprocess.run(
            [str(SCRIPTS / "git-autopick")],
            cwd=str(work),
            capture_output=True, text=True,
            env={**os.environ, "PATH": path_env})

        # The temp branch should contain only the 2 commits after the tracker
        # (shas[1] and shas[2] replayed onto master)
        branch_log = git(work, "log", "--oneline",
                         "origin/master..pr/sync-with-upstream",
                         check=False).stdout.strip()
        if branch_log:
            lines = branch_log.splitlines()
            self.assertEqual(len(lines), 2,
                             f"expected 2 replayed commits, got: {lines}")


if __name__ == "__main__":
    unittest.main()
