#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Test upstream tag synchronization with local Git repositories."""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from sync_tags import context_free_patch_id, equivalent_origin_commit, patch_id
from sync_tags import sync_all


def git(cwd: Path, *args: str, check: bool = True):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args} in {cwd}: {result.stderr}")
    return result


def init_bare(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(path.parent, "init", "--bare", str(path))
    return path


def configure_repo(repo: Path) -> None:
    git(repo, "config", "user.email", "test@test.com")
    git(repo, "config", "user.name", "Test")


def commit_file(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    git(repo, "add", filename)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


class TestSyncTags(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source_root = self.root / "src"

    def tearDown(self):
        self.tempdir.cleanup()

    def make_repo(self, name: str,
                  existing_tag: bool = False) -> tuple[dict, Path, Path,
                                                        str, str]:
        origin = init_bare(self.root / "remotes" / f"{name}-origin.git")
        upstream = init_bare(
            self.root / "remotes" / f"{name}-upstream.git")
        seed = self.root / "seed" / name
        seed.parent.mkdir(parents=True, exist_ok=True)
        git(seed.parent, "clone", str(origin), str(seed))
        configure_repo(seed)

        commit_file(
            seed, "base.txt",
            f"name {name}\nversion 6.28\ndep 6.28\nproject\nextensions off\n",
            "base")
        git(seed, "push", "origin", "master")
        git(seed, "remote", "add", "upstream", str(upstream))
        git(seed, "push", "upstream", "master")

        upstream_work = self.root / "upstream-work" / name
        upstream_work.parent.mkdir(parents=True, exist_ok=True)
        git(upstream_work.parent, "clone", str(upstream), str(upstream_work))
        configure_repo(upstream_work)
        upstream_previous = commit_file(
            upstream_work, "previous.txt", f"{name}-previous", "previous")
        git(upstream_work, "tag", "v6.28.0", upstream_previous)
        upstream_latest = commit_file(
            upstream_work, "base.txt",
            f"name {name}\nversion 6.29\ndep 6.29\nproject\nextensions off\n",
            "latest")
        git(upstream_work, "tag", "v6.29.0", upstream_latest)
        git(upstream_work, "push", "origin", "master", "v6.28.0",
            "v6.29.0")

        checkout = self.source_root / "sonicde" / name
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git(checkout.parent, "clone", str(origin), str(checkout))
        configure_repo(checkout)
        git(checkout, "remote", "add", "upstream", str(upstream))

        # A downstream-only commit makes the cherry-picks use different commit
        # IDs from upstream while preserving their exact patches.
        commit_file(
            checkout, "base.txt",
            f"name {name}\nversion 6.28\ndep 6.28\nproject\n"
            "export commands\nextensions off\n",
            "downstream")
        git(checkout, "fetch", "upstream", "master")
        git(checkout, "cherry-pick", upstream_previous)
        origin_previous = git(checkout, "rev-parse", "HEAD").stdout.strip()
        git(checkout, "cherry-pick", upstream_latest)
        origin_latest = git(checkout, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(
            patch_id(checkout, upstream_latest),
            patch_id(checkout, origin_latest),
            "fixture must require range-diff rather than exact patch IDs")
        self.assertEqual(
            context_free_patch_id(checkout, upstream_latest),
            context_free_patch_id(checkout, origin_latest),
            "equivalent changes must have the same context-free patch ID")
        git(checkout, "push", "origin", "master")

        if existing_tag:
            git(checkout, "tag", "6.29.0", origin_previous)
            git(checkout, "push", "origin", "6.29.0")

        package = {
            "name": f"sonicde/{name}",
            "git": {
                "remotes": {
                    "origin": {"url": str(origin)},
                    "upstream": {"url": str(upstream)},
                },
                "config": {
                    "autopick.enabled": "true",
                    "autopick.upstream": "upstream/master",
                    "autopick.master": "origin/master",
                    "autopick.tracker": "origin/tracking/master",
                },
            },
        }
        return package, checkout, origin, origin_previous, origin_latest

    def test_latest_tag_is_created_at_exact_equivalent_origin_commit(self):
        package, checkout, origin, previous, latest = self.make_repo("one")
        model = {
            "source_root": str(self.source_root),
            "packages": {"sonicde/one": package},
        }

        self.assertEqual(sync_all(model), 0)
        tagged = git(
            checkout, "ls-remote", "--tags", origin,
            "refs/tags/6.29.0").stdout.split()[0]
        self.assertEqual(tagged, latest)
        self.assertNotEqual(tagged, previous)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(sync_all(model), 0)
        self.assertEqual(output.getvalue(), "")

    def test_each_package_pushes_only_to_its_own_origin(self):
        pkg_a, checkout_a, origin_a, _, latest_a = self.make_repo("a")
        pkg_b, checkout_b, origin_b, _, latest_b = self.make_repo("b")
        model = {
            "source_root": str(self.source_root),
            "packages": {
                "sonicde/a": pkg_a,
                "sonicde/b": pkg_b,
            },
        }

        self.assertEqual(sync_all(model), 0)
        tag_a = git(
            checkout_a, "ls-remote", "--tags", origin_a,
            "refs/tags/6.29.0").stdout.split()[0]
        tag_b = git(
            checkout_b, "ls-remote", "--tags", origin_b,
            "refs/tags/6.29.0").stdout.split()[0]
        self.assertEqual(tag_a, latest_a)
        self.assertEqual(tag_b, latest_b)
        self.assertNotEqual(tag_a, tag_b)

    def test_existing_origin_tag_is_preserved_silently(self):
        package, checkout, origin, previous, latest = self.make_repo(
            "existing", existing_tag=True)
        model = {
            "source_root": str(self.source_root),
            "packages": {"sonicde/existing": package},
        }
        success_file = self.root / "state" / "success-sync-tag-repos.txt"
        output = io.StringIO()

        with mock.patch(
                "sync_tags.equivalent_origin_commit",
                side_effect=AssertionError(
                    "existing tags must skip equivalence lookup")):
            with contextlib.redirect_stdout(output):
                self.assertEqual(sync_all(
                    model, success_file=success_file), 0)

        remote_tag = git(
            checkout, "ls-remote", "--tags", origin,
            "refs/tags/6.29.0").stdout.split()[0]
        self.assertEqual(remote_tag, previous)
        self.assertNotEqual(remote_tag, latest)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(success_file.read_text(), "")

    def test_success_and_failure_tracker_files_are_replaced(self):
        package, _, _, _, _ = self.make_repo("success")
        missing = {
            "name": "sonicde/missing",
            "git": {
                "config": {
                    "autopick.enabled": "true",
                    "autopick.upstream": "upstream/master",
                    "autopick.master": "origin/master",
                    "autopick.tracker": "origin/tracking/master",
                },
            },
        }
        model = {
            "source_root": str(self.source_root),
            "packages": {
                "sonicde/success": package,
                "sonicde/missing": missing,
            },
        }
        failed_file = self.root / "state" / "failed-sync-tag-repos.txt"
        success_file = self.root / "state" / "success-sync-tag-repos.txt"
        errors = io.StringIO()

        with contextlib.redirect_stderr(errors):
            self.assertEqual(sync_all(
                model, failed_file=failed_file,
                success_file=success_file), 1)

        self.assertEqual(failed_file.read_text(), "missing\n")
        self.assertEqual(success_file.read_text(), "success: 6.29.0\n")
        self.assertIn("checkout missing", errors.getvalue())

        self.assertEqual(sync_all(
            model, only={"success"}, failed_file=failed_file,
            success_file=success_file), 0)
        self.assertEqual(failed_file.read_text(), "")
        self.assertEqual(success_file.read_text(), "")

    def test_empty_run_creates_tracker_files_immediately(self):
        failed_file = self.root / "state" / "failed-sync-tag-repos.txt"
        success_file = self.root / "state" / "success-sync-tag-repos.txt"
        model = {"source_root": str(self.source_root), "packages": {}}

        self.assertEqual(sync_all(
            model, failed_file=failed_file, success_file=success_file), 0)

        self.assertTrue(failed_file.exists())
        self.assertTrue(success_file.exists())
        self.assertEqual(failed_file.read_text(), "")
        self.assertEqual(success_file.read_text(), "")

    def test_completed_results_survive_an_interrupted_run(self):
        config = {
            "autopick.enabled": "true",
            "autopick.upstream": "upstream/master",
            "autopick.master": "origin/master",
            "autopick.tracker": "origin/tracking/master",
        }
        packages = {}
        for name in ("a", "b"):
            (self.source_root / "sonicde" / name / ".git").mkdir(
                parents=True)
            packages[f"sonicde/{name}"] = {
                "name": f"sonicde/{name}",
                "git": {"config": config},
            }
        model = {
            "source_root": str(self.source_root),
            "packages": packages,
        }
        failed_file = self.root / "state" / "failed-sync-tag-repos.txt"
        success_file = self.root / "state" / "success-sync-tag-repos.txt"

        with mock.patch(
                "sync_tags.sync_repo",
                side_effect=[("set tag 1.0 at abc", "1.0"),
                             KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                sync_all(model, failed_file=failed_file,
                         success_file=success_file)

        self.assertEqual(success_file.read_text(), "a: 1.0\n")
        self.assertEqual(failed_file.read_text(), "")

    def test_range_diff_similarity_cannot_match_different_changes(self):
        repo = self.root / "safety"
        repo.mkdir()
        git(repo, "init")
        configure_repo(repo)
        commit_file(repo, "value.txt", "base\n", "base")
        git(repo, "branch", "upstream")

        commit_file(repo, "value.txt", "origin value\n", "release update")
        git(repo, "switch", "upstream")
        upstream_commit = commit_file(
            repo, "value.txt", "upstream value\n", "release update")

        self.assertIsNone(equivalent_origin_commit(
            repo, upstream_commit, "upstream", "master"))


if __name__ == "__main__":
    unittest.main()
