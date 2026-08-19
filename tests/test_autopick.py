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
ORIGINAL_AUTOPICK = Path(
    "/home/joseph/Development/c++/sonicde-meta-build/scripts/git-autopick")


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


class TestOriginalScriptParity(unittest.TestCase):

    @unittest.skipUnless(ORIGINAL_AUTOPICK.exists(),
                         "authoritative sonicde-meta-build checkout unavailable")
    def test_builder_copy_matches_authoritative_original(self):
        """The port must not silently alter the known-good sync algorithm."""
        def executable_content(path: Path) -> bytes:
            lines = path.read_bytes().splitlines(keepends=True)
            return b"".join([
                lines[0],
                *(line for line in lines[1:]
                  if line.strip() and not line.lstrip().startswith(b"#")),
            ])

        self.assertEqual(
            executable_content(SCRIPTS / "git-autopick"),
            executable_content(ORIGINAL_AUTOPICK))


class TestAlreadySynced(AutopickTestBase):

    def test_tracker_equals_upstream_all_in_master(self):
        """Tracker == upstream, all changes in master -> already synced."""
        work, origin, upstream = self._setup_repo()
        r = self._run_autopick(work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already synced", r.stderr)


class TestNoChangesAfterRebase(AutopickTestBase):

    def test_patch_already_in_master_does_not_create_pr(self):
        """An upstream patch already represented in master produces no PR.

        The tracker remains behind upstream so the script must take its normal
        rebase path. Git drops the already-applied patch during rebase; the
        restored script must then detect HEAD == master, delete the temporary
        branch, advance the tracker, and never invoke gh or push a sync branch.
        """
        work, origin, upstream = self._setup_repo()

        # Add two independent commits to upstream after the tracker.
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")
        upstream_shas = []
        for i in range(1, 3):
            (up_work / f"upstream-{i}.txt").write_text(f"upstream {i}")
            git(up_work, "add", ".")
            git(up_work, "commit", "-m", f"upstream {i}")
            upstream_shas.append(
                git(up_work, "rev-parse", "HEAD").stdout.strip())
        git(up_work, "push", "origin", "master")
        git(work, "fetch", "upstream")

        # Apply the same patch independently to master, producing a different
        # commit identity with equivalent content.
        for i, upstream_sha in enumerate(upstream_shas, 1):
            git(work, "cherry-pick", "--no-commit", upstream_sha)
            git(work, "commit", "-m", f"downstream equivalent patch {i}")

        # Ensure the master tip itself is not patch-equivalent to the upstream
        # tip, so this exercises the post-rebase empty-work check rather than
        # the earlier one-tip patch-ID shortcut.
        (work / "downstream-only.txt").write_text("downstream")
        git(work, "add", ".")
        git(work, "commit", "-m", "downstream-only tip")
        master_sha = git(work, "rev-parse", "HEAD").stdout.strip()
        git(work, "push", "origin", "master")
        git(work, "fetch", "origin")

        # A fake gh that records any invocation. The no-change path must never
        # reach PR creation.
        gh_marker = self.root / "gh-called"
        fake_gh = self.root / "bin" / "gh"
        fake_gh.parent.mkdir(parents=True, exist_ok=True)
        fake_gh.write_text(f"""#!/bin/bash
touch {gh_marker}
exit 99
""")
        fake_gh.chmod(0o755)
        path_env = str(fake_gh.parent) + ":" + os.environ.get("PATH", "")

        r = subprocess.run(
            [str(SCRIPTS / "git-autopick")],
            cwd=str(work), capture_output=True, text=True,
            env={**os.environ, "PATH": path_env})

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no new changes after rebase", r.stderr)
        self.assertFalse(gh_marker.exists(), "gh was invoked for an empty sync")
        self.assertFalse(
            git(work, "show-ref", "--verify", "--quiet",
                "refs/heads/pr/sync-with-upstream", check=False).returncode == 0,
            "local temporary sync branch was not deleted")
        self.assertEqual(
            git(work, "ls-remote", "--heads", "origin",
                "pr/sync-with-upstream").stdout.strip(), "",
            "remote sync branch was pushed for an empty sync")
        self.assertEqual(
            git(work, "rev-parse", "origin/tracking/master").stdout.strip(),
            upstream_shas[-1],
            "tracker did not advance to the processed upstream tip")


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
        self.assertTrue(branch_log, "expected a sync branch with replayed commits")
        lines = branch_log.splitlines()
        self.assertEqual(len(lines), 2,
                         f"expected 2 replayed commits, got: {lines}")


if __name__ == "__main__":
    unittest.main()
