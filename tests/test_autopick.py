#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test autopick logic using local Git repositories.

No remote GitHub repositories are contacted. All Git operations use local
bare repos. The tracker is a branch on origin (matching real SonicDE setup).
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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


class TestPrMetadata(AutopickTestBase):

    def _run_sync(self, existing=False, fail="", open_pr=False,
                  metadata_status=0, recover=False):
        work, origin, upstream = self._setup_repo()
        tracker_before = git(work, "rev-parse", "origin/tracking/master").stdout.strip()
        tip = self._add_upstream_commits(upstream, 1)[-1]
        git(work, "fetch", "upstream")
        if existing:
            git(work, "push", "origin",
                f"{tip}:refs/heads/pr/sync-with-upstream")

        calls = self.root / "gh-calls"
        fake = self.root / "bin" / "gh"
        fake.parent.mkdir()
        retry_marker = self.root / "metadata-retried"
        fake_sleep = fake.with_name("sleep")
        fake_sleep.write_text("#!/bin/bash\nexit 0\n")
        fake_sleep.chmod(0o755)
        fake.write_text(f'''#!/bin/bash
printf '%s\\n' "$*" >> "{calls}"
case "$1 $2" in
    "repo set-default") exit 0 ;;
    "pr list")
        if [ "{open_pr}" = True ]; then echo 6; fi
        exit 0 ;;
    "pr create")
        for arg in "$@"; do
            case "$arg" in
                -a|--assignee|--reviewer) echo "unexpected GraphQL metadata" >&2; exit 2 ;;
            esac
        done
        [ "{fail}" != create ] || {{ echo 'creation failed' >&2; exit 1; }}
        echo 'https://github.com/Sonic-DE/test/pull/6'
        exit 0 ;;
    "api user")
        [ "{fail}" != user ] || {{ echo 'user lookup failed' >&2; exit 1; }}
        echo josephcrowell
        exit 0 ;;
    "api --method")
        case "$4" in
            repos/*/issues/6/assignees)
                [ "{fail}" != assignee ] || {{ echo 'assignment failed' >&2; exit 1; }} ;;
            repos/*/pulls/6/requested_reviewers)
                [ "{fail}" != reviewer ] || {{ echo 'review request failed' >&2; exit 1; }}
                [ "{fail}" != json_without_status ] || {{ echo 'unexpected end of JSON input' >&2; exit 1; }}
                if [ "{metadata_status}" -ne 0 ]; then
                    if [ "{recover}" != True ] || [ ! -f "{retry_marker}" ]; then
                        touch "{retry_marker}"
                        printf 'HTTP/2.0 {metadata_status} Error\\r\\nContent-Type: application/json\\r\\n\\r\\n'
                        echo 'unexpected end of JSON input' >&2
                        exit 1
                    fi
                fi ;;
            *) exit 2 ;;
        esac
        exit 0 ;;
    *) exit 2 ;;
esac
''')
        fake.chmod(0o755)
        result = subprocess.run(
            [str(SCRIPTS / "git-autopick")], cwd=work,
            capture_output=True, text=True,
            env={**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}"})
        tracker_after = git(work, "rev-parse", "origin/tracking/master").stdout.strip()
        commands = calls.read_text().splitlines()
        review_calls = [c for c in commands if "requested_reviewers" in c]
        if metadata_status:
            expected_attempts = 2 if recover else (3 if metadata_status in (500, 502, 503, 504) else 1)
            self.assertEqual(len(review_calls), expected_attempts, result.stderr)
            self.assertIn("unexpected end of JSON input", result.stderr)
            if metadata_status in (500, 502, 503, 504):
                self.assertIn(f"failed with HTTP {metadata_status}", result.stderr)
        if open_pr:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(tracker_after, tracker_before)
            self.assertFalse(any(c.startswith(("pr create", "api ")) for c in commands))
            return
        if fail or (metadata_status and not recover):
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(tracker_after, tracker_before)
            self.assertIn("tracker unchanged", result.stderr)
            if fail == "assignee":
                self.assertNotIn("requested_reviewers", "\n".join(commands))
            if fail == "json_without_status":
                self.assertEqual(len(review_calls), 1)
            return

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tracker_after, tip)
        self.assertEqual(len([c for c in commands if c.startswith("pr create")]), 1)
        updates = [c for c in commands if c.startswith("api --method")]
        repo = str(origin).removesuffix(".git")
        expected_updates = [
            f"api --method POST repos/{repo}/issues/6/assignees "
            "-f assignees[]=josephcrowell --include",
            f"api --method POST repos/{repo}/pulls/6/requested_reviewers "
            "-f team_reviewers[]=dev --include",
        ]
        if metadata_status and recover:
            expected_updates.append(expected_updates[-1])
        self.assertEqual(updates, expected_updates)

    def test_new_branch_requires_rest_metadata_before_tracker_update(self):
        self._run_sync()

    def test_existing_branch_requires_rest_metadata_before_tracker_update(self):
        self._run_sync(existing=True)

    def test_user_lookup_failure_preserves_tracker(self):
        self._run_sync(fail="user")

    def test_creation_failure_preserves_tracker(self):
        self._run_sync(fail="create")

    def test_assignee_failure_preserves_tracker(self):
        self._run_sync(fail="assignee")

    def test_reviewer_failure_preserves_tracker(self):
        self._run_sync(fail="reviewer")

    def test_existing_branch_reviewer_failure_preserves_tracker(self):
        self._run_sync(existing=True, fail="reviewer")

    def test_open_pr_skips_without_metadata_or_tracker_updates(self):
        self._run_sync(existing=True, open_pr=True)

    def test_empty_json_502_retries_existing_branch_metadata(self):
        self._run_sync(existing=True, metadata_status=502, recover=True)

    def test_empty_json_503_retries_new_branch_metadata(self):
        self._run_sync(metadata_status=503, recover=True)

    def test_persistent_empty_json_502_preserves_tracker(self):
        self._run_sync(metadata_status=502)

    def test_empty_json_422_is_not_retried(self):
        self._run_sync(metadata_status=422)

    def test_json_error_without_http_status_is_not_retried(self):
        self._run_sync(fail="json_without_status")

    @unittest.skipUnless(shutil.which("gh"), "GitHub CLI is not installed")
    def test_real_cli_retries_empty_json_502_from_local_server(self):
        """Exercise gh's actual JSON decoder, not just a fake CLI exit code."""
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))))
                status, body = (502, b"") if len(requests) == 1 else (201, b"{}")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        script = (SCRIPTS / "git-autopick").read_text()
        helper = "post_pr_metadata() {" + script.split(
            "post_pr_metadata() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
        try:
            result = subprocess.run(
                ["bash", "-c", 'msg() { printf "%s\\n" "$*" >&2; }\n' + helper
                 + 'post_pr_metadata "$1" -f "team_reviewers[]=dev"',
                 "test", f"http://127.0.0.1:{server.server_port}/requested_reviewers"],
                capture_output=True, text=True, timeout=20,
                env={**os.environ, "GH_TOKEN": "local-test-only",
                     "GITHUB_TOKEN": "local-test-only",
                     "GH_ENTERPRISE_TOKEN": "local-test-only",
                     "GITHUB_ENTERPRISE_TOKEN": "local-test-only",
                     "GH_HOST": "github.com", "GH_DEBUG": "",
                     "NO_PROXY": "127.0.0.1,localhost"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("unexpected end of JSON input", result.stderr)
            self.assertIn("failed with HTTP 502 (attempt 1/3)", result.stderr)
            self.assertEqual(requests, [{"team_reviewers": ["dev"]}] * 2)
            self.assertEqual(result.stdout, "")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class TestIgnoredAutomationCommits(AutopickTestBase):

    def _bot_author_env(self) -> dict[str, str]:
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": "l10n daemon script",
            "GIT_AUTHOR_EMAIL": "scripty@kde.org",
        }

    def _fake_gh(self, marker: Path = None) -> dict[str, str]:
        fake_gh = self.root / "bin" / "gh"
        fake_gh.parent.mkdir(parents=True, exist_ok=True)
        marker_command = f"touch {marker}\n" if marker else ""
        fake_gh.write_text(f"""#!/bin/bash
{marker_command}case "$1 $2" in
    "pr create") echo "https://github.com/Sonic-DE/test/pull/1" ;;
    "api user") echo josephcrowell ;;
esac
""")
        fake_gh.chmod(0o755)
        return {
            **os.environ,
            "PATH": str(fake_gh.parent) + ":" + os.environ.get("PATH", ""),
        }

    def test_bot_only_range_advances_tracker_without_pr(self):
        """A range containing only scripty commits is processed without a PR."""
        work, origin, upstream = self._setup_repo()
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")

        (up_work / "translation.po").write_text("automated translation")
        git(up_work, "add", ".")
        git(up_work, "commit", "-m", "SVN_SILENT made messages",
            env=self._bot_author_env())
        bot_sha = git(up_work, "rev-parse", "HEAD").stdout.strip()
        git(up_work, "push", "origin", "master")
        git(work, "fetch", "upstream")

        gh_marker = self.root / "gh-called"
        r = subprocess.run(
            [str(SCRIPTS / "git-autopick")],
            cwd=str(work), capture_output=True, text=True,
            env=self._fake_gh(gh_marker))

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ignoring upstream commit", r.stderr)
        self.assertIn("no new changes after rebase", r.stderr)
        self.assertFalse(gh_marker.exists(), "gh was invoked for ignored commits")
        self.assertEqual(
            git(work, "rev-parse", "origin/tracking/master").stdout.strip(),
            bot_sha,
            "tracker did not advance past the ignored bot commit")
        self.assertFalse(
            git(work, "cat-file", "-e", "origin/master:translation.po",
                check=False).returncode == 0,
            "ignored bot changes reached master")

    def test_bot_commit_is_removed_from_mixed_sync_branch(self):
        """Human changes are synced while adjacent scripty changes are dropped."""
        work, origin, upstream = self._setup_repo()
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")

        (up_work / "translation.po").write_text("automated translation")
        git(up_work, "add", ".")
        git(up_work, "commit", "-m", "SVN_SILENT made messages",
            env=self._bot_author_env())

        (up_work / "feature.txt").write_text("human change")
        git(up_work, "add", ".")
        git(up_work, "commit", "-m", "Add human feature")
        upstream_tip = git(up_work, "rev-parse", "HEAD").stdout.strip()
        git(up_work, "push", "origin", "master")
        git(work, "fetch", "upstream")

        r = subprocess.run(
            [str(SCRIPTS / "git-autopick")],
            cwd=str(work), capture_output=True, text=True,
            env=self._fake_gh())

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ignoring upstream commit", r.stderr)
        branch = "origin/pr/sync-with-upstream"
        self.assertEqual(
            git(work, "rev-list", "--count", f"origin/master..{branch}").stdout.strip(),
            "1")
        self.assertEqual(
            git(work, "show", f"{branch}:feature.txt").stdout,
            "human change")
        self.assertNotEqual(
            git(work, "cat-file", "-e", f"{branch}:translation.po",
                check=False).returncode,
            0,
            "ignored bot file was included in the sync branch")
        self.assertEqual(
            git(work, "rev-parse", "origin/tracking/master").stdout.strip(),
            upstream_tip,
            "tracker did not advance to the full upstream tip")


class TestIgnoredVersionBumpCommits(AutopickTestBase):

    def _fake_gh(self, marker: Path = None) -> dict[str, str]:
        fake_gh = self.root / "bin" / "gh"
        fake_gh.parent.mkdir(parents=True, exist_ok=True)
        marker_command = f"touch {marker}\n" if marker else ""
        fake_gh.write_text(f"""#!/bin/bash
{marker_command}case "$1 $2" in
    "pr create") echo "https://github.com/Sonic-DE/test/pull/1" ;;
    "api user") echo josephcrowell ;;
esac
""")
        fake_gh.chmod(0o755)
        return {
            **os.environ,
            "PATH": str(fake_gh.parent) + ":" + os.environ.get("PATH", ""),
        }

    def _commit_with_message(self, repo: Path, message: str, filename: str = None) -> str:
        target = repo / (filename or f"commit-{message[:10]}.txt")
        target.write_text(message)
        git(repo, "add", ".")
        git(repo, "commit", "-m", message)
        return git(repo, "rev-parse", "HEAD").stdout.strip()

    def test_version_bump_only_range_advances_tracker_without_pr(self):
        """A range containing only version bumps is processed without a PR."""
        work, origin, upstream = self._setup_repo()
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")

        for message in (
            "Update version for new release",
            "Update version to 6.7.5",
            "Update dependency version to 6.7.5",
            "GIT_SILENT Upgrade release service version to 26.08.0",
            "Upgrade release service version to 6.7.5",
        ):
            self._commit_with_message(up_work, message)
        upstream_tip = git(up_work, "rev-parse", "HEAD").stdout.strip()
        git(up_work, "push", "origin", "master")
        git(work, "fetch", "upstream")

        gh_marker = self.root / "gh-called"
        r = subprocess.run(
            [str(SCRIPTS / "git-autopick")],
            cwd=str(work), capture_output=True, text=True,
            env=self._fake_gh(gh_marker))

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no new changes after rebase", r.stderr)
        self.assertFalse(gh_marker.exists(), "gh was invoked for version bumps")
        self.assertEqual(
            git(work, "rev-parse", "origin/tracking/master").stdout.strip(),
            upstream_tip,
            "tracker did not advance past the version-bump commits")

    def test_version_bump_ignored_while_valid_commit_from_same_author_kept(self):
        """Adjacent valid commits from version-bump authors must still be synced."""
        work, origin, upstream = self._setup_repo()
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")

        (up_work / "version-bump.txt").write_text("bump")
        git(up_work, "add", ".")
        git(up_work, "commit", "-m", "Update version to 6.7.5")
        (up_work / "real-fix.txt").write_text("fix")
        git(up_work, "add", ".")
        git(up_work, "commit", "-m", "Add real fix")
        upstream_tip = git(up_work, "rev-parse", "HEAD").stdout.strip()
        git(up_work, "push", "origin", "master")
        git(work, "fetch", "upstream")

        r = subprocess.run(
            [str(SCRIPTS / "git-autopick")],
            cwd=str(work), capture_output=True, text=True,
            env=self._fake_gh())

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ignoring upstream commit", r.stderr)
        branch = "origin/pr/sync-with-upstream"
        self.assertEqual(
            git(work, "rev-list", "--count", f"origin/master..{branch}").stdout.strip(),
            "1",
            "expected exactly the valid commit to be synced")
        self.assertEqual(
            git(work, "log", "-1", "--format=%s", branch).stdout.strip(),
            "Add real fix",
            "expected the valid commit message")
        self.assertEqual(
            git(work, "show", f"{branch}:real-fix.txt").stdout,
            "fix")


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


class TestUptoTag(AutopickTestBase):
    """--upto-tag stops the sync at an upstream release tag."""

    def _upstream_with_tag(self, upstream: Path, tag_at: int, total: int,
                           tag: str = "v1.0") -> list[str]:
        up_work = self.root / "up_work"
        git(self.root, "clone", str(upstream), str(up_work))
        git(up_work, "config", "user.email", "test@test.com")
        git(up_work, "config", "user.name", "Test")
        shas = []
        for i in range(total):
            shas.append(commit(up_work, f"upstream {i+1}", f"up{i+1}"))
            if i + 1 == tag_at:
                git(up_work, "tag", "-a", tag, "-m", tag)
        git(up_work, "push", "--tags", "origin", "master")
        return shas

    def _run(self, work: Path, *args, marker: Path = None):
        fake_gh = self.root / "bin" / "gh"
        fake_gh.parent.mkdir(parents=True, exist_ok=True)
        touch = f"touch {marker}\n" if marker else ""
        fake_gh.write_text(f"""#!/bin/bash
{touch}case "$1 $2" in
    "pr create") echo "https://github.com/Sonic-DE/test/pull/1" ;;
    "api user") echo josephcrowell ;;
esac
""")
        fake_gh.chmod(0o755)
        return subprocess.run(
            [str(SCRIPTS / "git-autopick"), *args], cwd=str(work),
            capture_output=True, text=True,
            env={**os.environ,
                 "PATH": f"{fake_gh.parent}:{os.environ.get('PATH', '')}"})

    def _tracker(self, work: Path) -> str:
        git(work, "fetch", "origin")
        return git(work, "rev-parse", "origin/tracking/master").stdout.strip()

    def test_commits_after_the_tag_are_not_synced(self):
        work, origin, upstream = self._setup_repo()
        shas = self._upstream_with_tag(upstream, tag_at=2, total=3)
        git(work, "fetch", "upstream")

        r = self._run(work, "--upto-tag", "v1.0")

        self.assertEqual(r.returncode, 0, r.stderr)
        subjects = git(work, "log", "--format=%s",
                       "origin/master..origin/pr/sync-with-upstream").stdout.split("\n")
        self.assertEqual([s for s in subjects if s], ["upstream 2", "upstream 1"])
        self.assertEqual(self._tracker(work), shas[1])

    def test_tracker_past_the_tag_syncs_nothing(self):
        work, origin, upstream = self._setup_repo()
        shas = self._upstream_with_tag(upstream, tag_at=1, total=2)
        git(work, "fetch", "upstream")
        git(work, "push", "origin", f"+{shas[1]}:refs/heads/tracking/master")
        marker = self.root / "gh-called"

        r = self._run(work, "--upto-tag", "v1.0", marker=marker)

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already synced past v1.0", r.stderr)
        self.assertFalse(marker.exists(), "gh was called")
        self.assertEqual(self._tracker(work), shas[1])

    def test_tag_not_on_the_upstream_branch_is_refused(self):
        work, origin, upstream = self._setup_repo()
        self._upstream_with_tag(upstream, tag_at=1, total=1)
        up_work = self.root / "up_work"
        git(up_work, "checkout", "-q", "-b", "side", "HEAD~1")
        commit(up_work, "side commit", "side")
        git(up_work, "tag", "v2.0")
        git(up_work, "push", "origin", "v2.0")
        git(work, "fetch", "upstream")
        before = self._tracker(work)

        r = self._run(work, "--upto-tag", "v2.0")

        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tag v2.0 is not on upstream/master", r.stderr)
        self.assertEqual(self._tracker(work), before)

    def test_missing_tag_is_refused(self):
        work, origin, upstream = self._setup_repo()
        self._upstream_with_tag(upstream, tag_at=1, total=1)
        git(work, "fetch", "upstream")
        before = self._tracker(work)

        r = self._run(work, "--upto-tag", "v9.9")

        self.assertNotEqual(r.returncode, 0)
        self.assertIn("upstream has no tag v9.9", r.stderr)
        self.assertEqual(self._tracker(work), before)


class TestSyncTagLookup(unittest.TestCase):
    """sync passes the repository's tag, else its group's, to git-autopick."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        shutil.copy(SCRIPTS.parent / "sync", self.root / "sync")
        (self.root / "scripts").mkdir()
        fake = self.root / "scripts" / "git-autopick"
        fake.write_text('#!/bin/bash\necho "args:$*"\n')
        fake.chmod(0o755)
        (self.root / "config").mkdir()
        (self.root / "config" / "sync-upstream-tags").write_text(
            "# comment\ncore v6.8.2\nframeworks v6.31.0\nsonic-own v1.2.3\n"
            "sonic-win v6.8.1\n")
        (self.root / "scripts" / "version-repos-plasma.list").write_text(
            "# core\nsonic-win\nsonic-panel\n")
        (self.root / "scripts" / "version-repos-frameworks.list").write_text(
            "# frameworks\nsonic-frameworks-a\n")
        (self.root / "scripts" / "version-repos-system.list").write_text(
            "# system\nsonic-ecco\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _sync(self, name: str, enabled: bool = True):
        repo = self.root / "src" / "sonicde" / name
        repo.mkdir(parents=True)
        git(repo, "init", "-q")
        if enabled:
            git(repo, "config", "autopick.enabled", "true")
        return subprocess.run([str(self.root / "sync"), name],
                              capture_output=True, text=True)

    def test_group_tag(self):
        r = self._sync("sonic-panel")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "args:--upto-tag v6.8.2")

    def test_repository_line_overrides_group(self):
        r = self._sync("sonic-win")
        self.assertEqual(r.stdout.strip(), "args:--upto-tag v6.8.1")

    def test_ungrouped_repository_line(self):
        r = self._sync("sonic-own")
        self.assertEqual(r.stdout.strip(), "args:--upto-tag v1.2.3")

    def test_no_tag_refuses_to_sync(self):
        for name in ("sonic-ecco", "sonic-unlisted"):
            r = self._sync(name)
            self.assertNotEqual(r.returncode, 0, name)
            self.assertNotIn("args:", r.stdout, name)
            self.assertIn("no upstream release tag", r.stderr, name)

    def test_disabled_repository_needs_no_tag(self):
        r = self._sync("sonic-unlisted", enabled=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "args:")


class TestSyncTagsFile(unittest.TestCase):
    """Every line of config/sync-upstream-tags names a group or a known repository."""

    def test_names_are_groups_or_repositories(self):
        root = SCRIPTS.parent
        repos = set((root / "config" / "repos.list").read_text().split())
        groups = {"core", "frameworks", "system"}
        names = []
        for line in (root / "config" / "sync-upstream-tags").read_text().splitlines():
            fields = line.split()
            if not fields or fields[0].startswith("#"):
                continue
            self.assertEqual(len(fields), 2, line)
            names.append(fields[0])
            self.assertIn(fields[0], groups | repos, line)
        self.assertEqual(len(names), len(set(names)), "a name is listed twice")
        self.assertTrue(groups <= set(names), "a release group has no tag")


if __name__ == "__main__":
    unittest.main()
