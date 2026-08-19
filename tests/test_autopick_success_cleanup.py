#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test sync-success-cleanup against local Git repositories.

No remote GitHub repositories are contacted. The real
sync-success-cleanup script is run against a fixture project root
whose config.py/repositories.py are stubbed so only the per-repo reset
logic is exercised.

Regression: the old script ran `git checkout origin/master` which left
HEAD detached at the origin/master commit, and `git reset --hard
origin/master` while detached only moved the detached HEAD — it never
advanced the local master branch. These tests pin the fixed behavior:
after cleanup, HEAD must be attached to the local `master` branch at
origin/master's tip.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "sync-success-cleanup"


def git(cwd, *args, check=True):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} in {cwd}: {r.stderr}")
    return r


def write_stub_config(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("""#!/usr/bin/env python3
import json, os, sys
out = "state/model.json"
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "-out" and i + 1 < len(args):
        out = args[i + 1]
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
with open(out, "w") as f:
    json.dump({"topo_order": [], "packages": {}}, f)
""")


def write_stub_repositories(path: Path, repo_name: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""#!/usr/bin/env python3
import sys
out = "repo-list"
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "--out" and i + 1 < len(args):
        out = args[i + 1]
with open(out, "w") as f:
    f.write("{repo_name}\\n")
""")


def init_bare(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    git(path.parent, "init", "--bare", str(path))


def commit(path: Path, msg: str, content: str = None):
    (path / "file.txt").write_text(content if content is not None else msg)
    git(path, "add", ".")
    git(path, "commit", "-m", msg)
    return git(path, "rev-parse", "HEAD").stdout.strip()


class SuccessCleanupTestBase(unittest.TestCase):

    REPO = "testrepo"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

        # Fixture project root: stub scripts + real sync-success-cleanup
        write_stub_config(self.root / "scripts" / "config.py")
        write_stub_repositories(self.root / "scripts" / "repositories.py",
                                self.REPO)
        dst_script = self.root / "sync-success-cleanup"
        shutil.copy(SCRIPT, dst_script)
        dst_script.chmod(0o755)

        # Bare origin remote
        self.origin = self.root / "remotes" / "origin.git"
        init_bare(self.origin)

        # Driver clone to push commits to origin
        self.driver = self.root / "driver"
        git(self.root, "clone", str(self.origin), str(self.driver))
        git(self.driver, "config", "user.email", "test@test.com")
        git(self.driver, "config", "user.name", "Test")

        # Checkout the script will operate on
        self.checkout = self.root / "src" / "sonicde" / self.REPO
        self.checkout.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _clone_checkout(self):
        git(self.root, "clone", str(self.origin), str(self.checkout))
        git(self.checkout, "config", "user.email", "test@test.com")
        git(self.checkout, "config", "user.name", "Test")

    def _run_script(self):
        return subprocess.run([str(self.root / "sync-success-cleanup")],
                              cwd=str(self.root),
                              capture_output=True, text=True)

    def _branch(self, cwd):
        r = git(cwd, "symbolic-ref", "--short", "HEAD", check=False)
        return r.stdout.strip()

    def _sha(self, cwd, ref):
        return git(cwd, "rev-parse", ref).stdout.strip()

    def _has_branch(self, cwd, name):
        r = git(cwd, "show-ref", "--verify", "--quiet",
                f"refs/heads/{name}", check=False)
        return r.returncode == 0


class TestResetAdvancesMaster(SuccessCleanupTestBase):

    def test_master_advances_and_head_attached(self):
        """Local master behind origin/master, with a merged PR branch.

        After cleanup: HEAD is on local master (not detached), master
        == origin/master, and pr/sync-with-upstream is deleted.
        """
        c0 = commit(self.driver, "initial")
        git(self.driver, "push", "origin", "master")

        self._clone_checkout()  # master + origin/master both at c0

        # Advance origin/master past local master (simulates merged PR)
        c1 = commit(self.driver, "merged-upstream", "up1")
        git(self.driver, "push", "origin", "master")

        # Create the merged PR branch at local master (c0)
        git(self.checkout, "branch", "pr/sync-with-upstream", "master")
        # Make sure checkout is clean and on master
        git(self.checkout, "checkout", "-q", "master")
        git(self.checkout, "reset", "--hard", "origin/master",
            check=False)  # no-op pre-fetch; just ensure clean

        r = self._run_script()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

        # HEAD must be attached to master (regression: was detached)
        self.assertEqual(self._branch(self.checkout), "master")
        # Local master advanced to origin/master tip
        self.assertEqual(self._sha(self.checkout, "master"), c1)
        self.assertEqual(self._sha(self.checkout, "HEAD"), c1)
        self.assertEqual(self._sha(self.checkout, "origin/master"), c1)
        # Merged PR branch deleted
        self.assertFalse(self._has_branch(self.checkout,
                                          "pr/sync-with-upstream"))
        # No warning emitted about detached HEAD
        self.assertNotIn("detached", r.stdout)


class TestCreatesMasterWhenMissing(SuccessCleanupTestBase):

    def test_master_created_from_origin_master(self):
        """No local master branch and HEAD detached at origin/master.

        After cleanup: master is created tracking origin/master and HEAD
        is attached to it (regression: previously HEAD stayed detached).
        """
        c0 = commit(self.driver, "initial")
        git(self.driver, "push", "origin", "master")

        self._clone_checkout()

        # Advance origin/master
        c1 = commit(self.driver, "second", "up1")
        git(self.driver, "push", "origin", "master")

        # Detach HEAD at origin/master and delete local master
        git(self.checkout, "fetch", "origin")
        git(self.checkout, "checkout", "-q", "origin/master")  # detached
        git(self.checkout, "branch", "-D", "master")
        self.assertEqual(self._branch(self.checkout), "")  # detached

        r = self._run_script()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

        self.assertEqual(self._branch(self.checkout), "master")
        self.assertTrue(self._has_branch(self.checkout, "master"))
        self.assertEqual(self._sha(self.checkout, "master"), c1)
        self.assertEqual(self._sha(self.checkout, "HEAD"), c1)
        self.assertNotIn("detached", r.stdout)


class TestDirtyTreeSkipped(SuccessCleanupTestBase):

    def test_dirty_worktree_skipped(self):
        """A dirty worktree is skipped (not reset), leaving HEAD as-is."""
        c0 = commit(self.driver, "initial")
        git(self.driver, "push", "origin", "master")

        self._clone_checkout()

        c1 = commit(self.driver, "second", "up1")
        git(self.driver, "push", "origin", "master")

        git(self.checkout, "checkout", "-q", "master")
        # Dirty the worktree
        (self.checkout / "uncommitted.txt").write_text("local change")
        head_before = self._sha(self.checkout, "HEAD")

        r = self._run_script()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("dirty worktree", r.stdout)

        # Unchanged: still on master at old commit
        self.assertEqual(self._branch(self.checkout), "master")
        self.assertEqual(self._sha(self.checkout, "HEAD"), head_before)


if __name__ == "__main__":
    unittest.main()
