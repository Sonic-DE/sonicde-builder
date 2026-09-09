#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test fetch behavior against local Git repositories."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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

    def test_retry_after_failed_initial_fetch_populates_sources(self):
        """A failed upstream fetch must not strand an empty .git checkout."""
        origin = self._make_remote("retry-origin")
        upstream = self._make_remote("retry-upstream")
        spec = {
            "ref": "origin/master", "local_branch": "master",
            "remotes": {"origin": {"url": str(origin)},
                        "upstream": {"url": str(self.root / "missing.git")}},
        }
        model = self._model("sonicde/retry", spec)
        self.assertEqual(fetch_all(model), 1)
        dest = self.source_root / "sonicde/retry"
        self.assertTrue(is_git_repo(dest))
        self.assertFalse((dest / "file.txt").exists())
        spec["remotes"]["upstream"]["url"] = str(upstream)
        self.assertEqual(fetch_all(model), 0)
        self.assertEqual((dest / "file.txt").read_text(), "test")
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=dest,
                              check=True, capture_output=True, text=True).stdout
        self.assertEqual(fetch_all(model), 0)
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest,
                                        check=True, capture_output=True, text=True).stdout, head)

    def test_empty_init_can_resume_detached_checkout(self):
        remote = self._make_remote("detached-retry")
        dest = self.source_root / "sonicde/detached-retry"
        dest.mkdir(parents=True)
        subprocess.run(["git", "init", str(dest)], check=True, capture_output=True)
        model = self._model("sonicde/detached-retry", {
            "ref": "origin/master", "remotes": {"origin": {"url": str(remote)}}})
        self.assertEqual(fetch_all(model, dry_run=True), 0)
        self.assertFalse((dest / "file.txt").exists())
        self.assertEqual(fetch_all(model), 0)
        self.assertTrue((dest / "file.txt").exists())
        result = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=dest, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_unborn_checkout_with_local_files_is_not_overwritten(self):
        remote = self._make_remote("local-data")
        dest = self.source_root / "sonicde/local-data"
        dest.mkdir(parents=True)
        subprocess.run(["git", "init", str(dest)], check=True, capture_output=True)
        (dest / "file.txt").write_text("preserve this")
        model = self._model("sonicde/local-data", {
            "ref": "origin/master", "local_branch": "master",
            "remotes": {"origin": {"url": str(remote)}}})
        self.assertEqual(fetch_all(model), 1)
        self.assertEqual((dest / "file.txt").read_text(), "preserve this")
        result = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=dest, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_empty_worktree_with_staged_deletions_stays_untouched(self):
        remote = self._make_remote("deleted-source")
        model = self._model("sonicde/deleted-source", {
            "ref": "origin/master", "local_branch": "master",
            "remotes": {"origin": {"url": str(remote)}}})
        self.assertEqual(fetch_all(model), 0)
        dest = self.source_root / "sonicde/deleted-source"
        subprocess.run(["git", "rm", "file.txt"], cwd=dest, check=True, capture_output=True)
        before = subprocess.run(["git", "status", "--porcelain"], cwd=dest,
                                check=True, capture_output=True, text=True).stdout
        self.assertEqual(fetch_all(model), 0)
        self.assertFalse((dest / "file.txt").exists())
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=dest,
                                        check=True, capture_output=True, text=True).stdout, before)

    def test_unborn_checkout_with_index_only_data_is_not_recovered(self):
        remote = self._make_remote("index-data")
        dest = self.source_root / "sonicde/index-data"
        dest.mkdir(parents=True)
        subprocess.run(["git", "init", str(dest)], check=True, capture_output=True)
        local = dest / "file.txt"
        local.write_text("staged data")
        subprocess.run(["git", "add", "file.txt"], cwd=dest, check=True, capture_output=True)
        local.unlink()
        model = self._model("sonicde/index-data", {
            "ref": "origin/master", "local_branch": "master",
            "remotes": {"origin": {"url": str(remote)}}})
        self.assertEqual(fetch_all(model), 1)
        self.assertFalse(local.exists())
        stored = subprocess.run(["git", "show", ":file.txt"], cwd=dest,
                                check=True, capture_output=True, text=True).stdout
        self.assertEqual(stored, "staged data")

    def test_unborn_checkout_with_ignored_files_is_not_recovered(self):
        remote = self._make_remote("ignored-data")
        dest = self.source_root / "sonicde/ignored-data"
        dest.mkdir(parents=True)
        subprocess.run(["git", "init", str(dest)], check=True, capture_output=True)
        (dest / ".git/info/exclude").write_text("file.txt\n")
        (dest / "file.txt").write_text("ignored local data")
        model = self._model("sonicde/ignored-data", {
            "ref": "origin/master", "local_branch": "master",
            "remotes": {"origin": {"url": str(remote)}}})
        self.assertEqual(fetch_all(model), 1)
        self.assertEqual((dest / "file.txt").read_text(), "ignored local data")

    def test_existing_checkout_initializes_missing_submodule_and_preserves_changes(self):
        child = self._make_remote("submodule-child")
        parent = self._make_remote("submodule-parent")
        work = self.root / "work/submodule-parent"
        subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add",
                        str(child), "lib/interfaces"], cwd=work, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-am", "add interfaces"], cwd=work,
                       check=True, capture_output=True)
        subprocess.run(["git", "push", "origin", "master"], cwd=work,
                       check=True, capture_output=True)

        dest = self.source_root / "sonicde/submodule-parent"
        dest.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", str(parent), str(dest)], check=True, capture_output=True)
        self.assertFalse((dest / "lib/interfaces/file.txt").exists())
        model = self._model("sonicde/submodule-parent", {
            "ref": "origin/master", "local_branch": "master",
            "remotes": {"origin": {"url": str(parent)}},
        })
        with mock.patch.dict(os.environ, {"GIT_ALLOW_PROTOCOL": "file"}):
            self.assertEqual(fetch_all(model), 0)
        nested = dest / "lib/interfaces/file.txt"
        self.assertTrue(nested.exists())

        nested.write_text("preserve local submodule work")
        with mock.patch.dict(os.environ, {"GIT_ALLOW_PROTOCOL": "file"}):
            self.assertEqual(fetch_all(model), 0)
        self.assertEqual(nested.read_text(), "preserve local submodule work")


if __name__ == "__main__":
    unittest.main()
