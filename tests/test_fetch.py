#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test fetch behavior against local Git repositories."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch import fetch_all, is_git_repo, redact_url


def init_bare_remote(path: Path) -> Path:
    """Create a bare remote repo and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(path)],
                   capture_output=True, check=True)
    return path


def make_commit(repo: Path, msg: str = "test") -> str:
    """Make a commit in the given repo, return its SHA."""
    (repo / "file.txt").write_text(msg)
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=str(repo),
                   capture_output=True)
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                       capture_output=True, text=True)
    return r.stdout.strip()


class TestFetch(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.source_root = self.root / "src"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_remote(self, name: str = "test-repo") -> Path:
        """Create a remote repo with one commit."""
        remote = self.root / "remotes" / f"{name}.git"
        init_bare_remote(remote)
        # clone, commit, push
        work = self.root / "work" / name
        work.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", str(remote), str(work)],
                       capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(work), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(work), capture_output=True)
        make_commit(work)
        subprocess.run(["git", "push", "origin", "master"],
                       cwd=str(work), capture_output=True, check=True)
        return remote

    def _model(self, name: str, git_spec: dict) -> dict:
        return {
            "source_root": str(self.source_root),
            "packages": {name: {"git": git_spec}},
            "topo_order": [name],
            "edges": [],
            "reverse": {},
        }

    def test_clone_new_checkout(self):
        """A missing checkout is cloned correctly."""
        remote = self._make_remote("test-clone")
        model = self._model("sonicde/test-clone", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {"origin": {
                "url": str(remote),
                "fetch": ["refs/heads/*:refs/remotes/origin/*"],
            }},
            "config": {"advice.detachedHead": "false"},
        })
        errs = fetch_all(model)
        self.assertEqual(errs, 0)
        dest = self.source_root / "sonicde" / "test-clone"
        self.assertTrue(is_git_repo(dest))
        # verify local branch exists
        r = subprocess.run(["git", "branch", "--list", "master"],
                           cwd=str(dest), capture_output=True, text=True)
        self.assertIn("master", r.stdout)

    def test_existing_checkout_preserved(self):
        """An existing checkout is fetched without HEAD mutation."""
        remote = self._make_remote("test-existing")
        model = self._model("sonicde/test-existing", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {"origin": {
                "url": str(remote),
                "fetch": ["refs/heads/*:refs/remotes/origin/*"],
            }},
        })
        # First fetch
        fetch_all(model)
        dest = self.source_root / "sonicde" / "test-existing"
        # Make a local change (untracked file)
        (dest / "untracked.txt").write_text("local")
        old_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(dest),
            capture_output=True, text=True).stdout.strip()

        # Add a new commit to the remote
        work = self.root / "work" / "test-existing"
        make_commit(work, "second")
        subprocess.run(["git", "push"], cwd=str(work), capture_output=True)

        # Second fetch
        errs = fetch_all(model)
        self.assertEqual(errs, 0)
        # Untracked file preserved
        self.assertTrue((dest / "untracked.txt").exists())
        # HEAD should still be the old commit (no checkout mutation)
        new_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(dest),
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(old_head, new_head)

    def test_tags_fetched_for_new_and_existing_checkout(self):
        """Tags are fetched even when the remote disables implicit tags."""
        remote = self._make_remote("test-tags")
        work = self.root / "work" / "test-tags"
        subprocess.run(["git", "tag", "v1"], cwd=str(work),
                       capture_output=True, check=True)
        subprocess.run(["git", "push", "origin", "v1"], cwd=str(work),
                       capture_output=True, check=True)

        model = self._model("sonicde/test-tags", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {"origin": {
                "url": str(remote),
                "fetch": ["refs/heads/*:refs/remotes/origin/*"],
                "tagopt": "--no-tags",
            }},
        })
        self.assertEqual(fetch_all(model), 0)
        dest = self.source_root / "sonicde" / "test-tags"
        self.assertEqual(subprocess.run(
            ["git", "tag", "--list", "v1"], cwd=str(dest),
            capture_output=True, text=True, check=True).stdout.strip(), "v1")

        subprocess.run(["git", "tag", "v2"], cwd=str(work),
                       capture_output=True, check=True)
        subprocess.run(["git", "push", "origin", "v2"], cwd=str(work),
                       capture_output=True, check=True)
        self.assertEqual(fetch_all(model), 0)
        self.assertEqual(subprocess.run(
            ["git", "tag", "--list", "v2"], cwd=str(dest),
            capture_output=True, text=True, check=True).stdout.strip(), "v2")

    def test_dry_run_no_mutation(self):
        """Dry run does not create or mutate any repositories."""
        remote = self._make_remote("test-dry")
        model = self._model("sonicde/test-dry", {
            "ref": "origin/master",
            "remotes": {"origin": {"url": str(remote)}},
        })
        errs = fetch_all(model, dry_run=True)
        self.assertEqual(errs, 0)
        dest = self.source_root / "sonicde" / "test-dry"
        self.assertFalse(dest.exists())

    def test_origin_only_no_fabricated_upstream(self):
        """An origin-only package does not get a fabricated upstream remote."""
        remote = self._make_remote("test-origin-only")
        model = self._model("sonicde/test-origin-only", {
            "ref": "origin/master",
            "remotes": {"origin": {
                "url": str(remote),
                "fetch": ["refs/heads/*:refs/remotes/origin/*"],
            }},
        })
        fetch_all(model)
        dest = self.source_root / "sonicde" / "test-origin-only"
        r = subprocess.run(["git", "remote"], cwd=str(dest),
                           capture_output=True, text=True)
        remotes = r.stdout.split()
        self.assertIn("origin", remotes)
        self.assertNotIn("upstream", remotes)

    def test_url_redaction(self):
        """Credentials in URLs are redacted for logging."""
        url = "https://user:token@github.com/org/repo.git"
        redacted = redact_url(url)
        self.assertNotIn("token", redacted)
        self.assertIn("***", redacted)


if __name__ == "__main__":
    unittest.main()
