#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Comprehensive fetch tests using local bare Git repositories.

No remote repositories are contacted. All fixtures are local bare repos.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fetch import fetch_all, is_git_repo, redact_url, fetch_missing, fetch_existing


def git(cwd, *args, check=True):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} in {cwd}: {r.stderr}")
    return r


def make_bare_remote(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(path.parent, "init", "--bare", str(path))
    return path


def make_work_repo(path: Path, remote: Path, name: str = "test") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(path.parent, "clone", str(remote), str(path))
    git(path, "config", "user.email", "test@test.com")
    git(path, "config", "user.name", "Test")
    return path


def commit(path: Path, msg: str = "test", content: str = None) -> str:
    f = path / "file.txt"
    f.write_text(content if content is not None else msg)
    git(path, "add", ".")
    git(path, "commit", "-m", msg)
    return git(path, "rev-parse", "HEAD").stdout.strip()


def tag(path: Path, tagname: str):
    git(path, "tag", tagname)


class TestFetchMultiRemote(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.source_root = self.root / "src"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _model(self, name: str, git_spec: dict) -> dict:
        return {
            "source_root": str(self.source_root),
            "packages": {name: {"git": git_spec}},
            "topo_order": [name],
            "edges": [],
            "reverse": {},
        }

    def test_two_remotes_origin_and_upstream(self):
        """A package with origin and upstream remotes fetches both."""
        origin_remote = self.root / "remotes" / "origin.git"
        upstream_remote = self.root / "remotes" / "upstream.git"
        make_bare_remote(origin_remote)
        make_bare_remote(upstream_remote)

        # populate both
        w1 = make_work_repo(self.root / "w1", origin_remote, "origin")
        commit(w1, "origin commit")
        git(w1, "push", "origin", "master")

        w2 = make_work_repo(self.root / "w2", upstream_remote, "upstream")
        commit(w2, "upstream commit")
        git(w2, "push", "origin", "master")

        model = self._model("sonicde/test", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {
                "origin": {
                    "url": str(origin_remote),
                    "fetch": ["refs/heads/*:refs/remotes/origin/*"],
                },
                "upstream": {
                    "url": str(upstream_remote),
                    "fetch": ["refs/heads/*:refs/remotes/upstream/*"],
                },
            },
            "config": {
                "advice.detachedHead": "false",
                "remote.origin.tagOpt": "--no-tags",
                "remote.upstream.tagOpt": "--no-tags",
            },
        })
        errs = fetch_all(model)
        self.assertEqual(errs, 0)
        dest = self.source_root / "sonicde" / "test"
        self.assertTrue(is_git_repo(dest))

        # both remotes should exist
        remotes = git(dest, "remote").stdout.split()
        self.assertIn("origin", remotes)
        self.assertIn("upstream", remotes)

        # tagOpt should be set
        tagopt = git(dest, "config", "remote.origin.tagOpt").stdout.strip()
        self.assertEqual(tagopt, "--no-tags")
        tagopt_u = git(dest, "config", "remote.upstream.tagOpt").stdout.strip()
        self.assertEqual(tagopt_u, "--no-tags")

        # advice.detachedHead should be set
        advice = git(dest, "config", "advice.detachedHead").stdout.strip()
        self.assertEqual(advice, "false")

        # upstream remote-tracking branch should exist
        r = git(dest, "rev-parse", "upstream/master", check=False)
        self.assertEqual(r.returncode, 0)

    def test_fetch_refspecs_configured(self):
        """Fetch refspecs are added to remote config."""
        origin_remote = self.root / "remotes" / "origin.git"
        make_bare_remote(origin_remote)
        w = make_work_repo(self.root / "w", origin_remote)
        commit(w, "init")
        git(w, "push", "origin", "master")

        model = self._model("sonicde/test", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {
                "origin": {
                    "url": str(origin_remote),
                    "fetch": [
                        "refs/heads/*:refs/remotes/origin/*",
                        "refs/tags/*:refs/tags/origin/*",
                    ],
                },
            },
        })
        fetch_all(model)
        dest = self.source_root / "sonicde" / "test"
        r = git(dest, "config", "--get-all", "remote.origin.fetch")
        refspecs = r.stdout.strip().splitlines()
        self.assertIn("refs/heads/*:refs/remotes/origin/*", refspecs)
        self.assertIn("refs/tags/*:refs/tags/origin/*", refspecs)

    def test_depth_option(self):
        """Depth option is passed to git fetch."""
        origin_remote = self.root / "remotes" / "origin.git"
        make_bare_remote(origin_remote)
        w = make_work_repo(self.root / "w", origin_remote)
        commit(w, "c1")
        commit(w, "c2")
        commit(w, "c3")
        git(w, "push", "origin", "master")

        model = self._model("sonicde/test", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {
                "origin": {
                    "url": str(origin_remote),
                    "depth": 1,
                    "fetch": ["refs/heads/*:refs/remotes/origin/*"],
                },
            },
        })
        fetch_all(model)
        dest = self.source_root / "sonicde" / "test"
        # With depth=1, only 1 commit should be reachable
        count = int(git(dest, "rev-list", "--count", "HEAD").stdout.strip())
        self.assertEqual(count, 1)

    def test_existing_checkout_remote_url_repair(self):
        """Existing checkout with wrong origin URL gets repaired."""
        origin_remote = self.root / "remotes" / "origin.git"
        wrong_remote = self.root / "remotes" / "wrong.git"
        make_bare_remote(origin_remote)
        make_bare_remote(wrong_remote)

        w = make_work_repo(self.root / "w", origin_remote)
        commit(w, "init")
        git(w, "push", "origin", "master")

        model = self._model("sonicde/test", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {
                "origin": {
                    "url": str(origin_remote),
                    "fetch": ["refs/heads/*:refs/remotes/origin/*"],
                },
            },
        })
        # First fetch
        fetch_all(model)
        dest = self.source_root / "sonicde" / "test"

        # Sabotage the origin URL
        git(dest, "remote", "set-url", "origin", str(wrong_remote))

        # Re-fetch should repair the URL
        errs = fetch_all(model)
        self.assertEqual(errs, 0)
        actual_url = git(dest, "remote", "get-url", "origin").stdout.strip()
        self.assertEqual(actual_url, str(origin_remote))

    def test_existing_checkout_adds_missing_remote(self):
        """Existing checkout gets a missing upstream remote added."""
        origin_remote = self.root / "remotes" / "origin.git"
        upstream_remote = self.root / "remotes" / "upstream.git"
        make_bare_remote(origin_remote)
        make_bare_remote(upstream_remote)

        w1 = make_work_repo(self.root / "w1", origin_remote)
        commit(w1, "init")
        git(w1, "push", "origin", "master")

        w2 = make_work_repo(self.root / "w2", upstream_remote)
        commit(w2, "upstream init")
        git(w2, "push", "origin", "master")

        # First fetch with origin only
        model_origin_only = self._model("sonicde/test", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {
                "origin": {
                    "url": str(origin_remote),
                    "fetch": ["refs/heads/*:refs/remotes/origin/*"],
                },
            },
        })
        fetch_all(model_origin_only)
        dest = self.source_root / "sonicde" / "test"
        remotes = git(dest, "remote").stdout.split()
        self.assertNotIn("upstream", remotes)

        # Now fetch with both remotes
        model_both = self._model("sonicde/test", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {
                "origin": {
                    "url": str(origin_remote),
                    "fetch": ["refs/heads/*:refs/remotes/origin/*"],
                },
                "upstream": {
                    "url": str(upstream_remote),
                    "fetch": ["refs/heads/*:refs/remotes/upstream/*"],
                },
            },
        })
        errs = fetch_all(model_both)
        self.assertEqual(errs, 0)
        remotes = git(dest, "remote").stdout.split()
        self.assertIn("upstream", remotes)

    def test_existing_checkout_preserves_branch_and_index(self):
        """Existing checkout keeps its branch, index, and untracked files."""
        origin_remote = self.root / "remotes" / "origin.git"
        make_bare_remote(origin_remote)
        w = make_work_repo(self.root / "w", origin_remote)
        commit(w, "c1")
        git(w, "push", "origin", "master")

        model = self._model("sonicde/test", {
            "ref": "origin/master",
            "local_branch": "master",
            "remotes": {
                "origin": {
                    "url": str(origin_remote),
                    "fetch": ["refs/heads/*:refs/remotes/origin/*"],
                },
            },
        })
        fetch_all(model)
        dest = self.source_root / "sonicde" / "test"

        # Create untracked file and a local change
        (dest / "untracked.txt").write_text("local")
        (dest / "file.txt").write_text("modified locally")
        git(dest, "add", "file.txt")
        git(dest, "commit", "-m", "local change")
        old_head = git(dest, "rev-parse", "HEAD").stdout.strip()

        # Add new commit to remote
        commit(w, "c2")
        git(w, "push", "origin", "master")

        # Re-fetch
        errs = fetch_all(model)
        self.assertEqual(errs, 0)

        # Untracked file preserved
        self.assertTrue((dest / "untracked.txt").exists())
        # HEAD unchanged
        new_head = git(dest, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(old_head, new_head)
        # Still on master branch
        branch = git(dest, "branch", "--show-current").stdout.strip()
        self.assertEqual(branch, "master")

    def test_fetch_only_includes_prerequisites(self):
        """--only limits fetch to requested packages and their prerequisites."""
        origin1 = self.root / "remotes" / "a.git"
        origin2 = self.root / "remotes" / "b.git"
        make_bare_remote(origin1)
        make_bare_remote(origin2)
        for r, w in [(origin1, "wa"), (origin2, "wb")]:
            w = make_work_repo(self.root / w, r)
            commit(w, "init")
            git(w, "push", "origin", "master")

        model = {
            "source_root": str(self.source_root),
            "packages": {
                "sonicde/a": {"git": {
                    "ref": "origin/master", "local_branch": "master",
                    "remotes": {"origin": {"url": str(origin1),
                     "fetch": ["refs/heads/*:refs/remotes/origin/*"]}},
                }},
                "sonicde/b": {"git": {
                    "ref": "origin/master", "local_branch": "master",
                    "remotes": {"origin": {"url": str(origin2),
                     "fetch": ["refs/heads/*:refs/remotes/origin/*"]}},
                }},
            },
            "topo_order": ["sonicde/a", "sonicde/b"],
            "edges": [],
            "reverse": {},
        }
        # Only fetch sonicde/a
        errs = fetch_all(model, only=["sonicde/a"])
        self.assertEqual(errs, 0)
        self.assertTrue(is_git_repo(self.source_root / "sonicde" / "a"))
        self.assertFalse((self.source_root / "sonicde" / "b").exists())

    def test_dry_run_creates_nothing(self):
        """Dry run creates no directories."""
        origin = self.root / "remotes" / "origin.git"
        make_bare_remote(origin)
        w = make_work_repo(self.root / "w", origin)
        commit(w, "init")
        git(w, "push", "origin", "master")

        model = self._model("sonicde/test", {
            "ref": "origin/master",
            "remotes": {"origin": {"url": str(origin)}},
        })
        errs = fetch_all(model, dry_run=True)
        self.assertEqual(errs, 0)
        self.assertFalse((self.source_root / "sonicde" / "test").exists())

    def test_url_redaction(self):
        """Credentials are redacted in URLs."""
        self.assertEqual(redact_url("https://user:token@github.com/org/repo.git"),
                         "https://***@github.com/org/repo.git")
        self.assertEqual(redact_url("https://github.com/org/repo.git"),
                         "https://github.com/org/repo.git")
        self.assertEqual(redact_url("git@github.com:org/repo.git"),
                         "git@github.com:org/repo.git")


if __name__ == "__main__":
    unittest.main()
