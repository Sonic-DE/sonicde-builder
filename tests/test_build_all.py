# SPDX-License-Identifier: GPL-2.0-only
"""Exercise wrapper routing and the full-build deployment barrier, offline."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def wrapper(tmp_path):
    script = tmp_path / "build-all"
    shutil.copy2(ROOT / "build-all", script)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.jsonl"
    for command in ("python3", "cmake", "sudo"):
        executable = binaries / command
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['COMMAND_LOG'], 'a') as f:\n"
            "    f.write(json.dumps([Path(sys.argv[0]).name] + args) + '\\n')\n"
            "if os.environ.get('FAIL_BUILD') and args[:2] == ['--build', 'build']:\n"
            "    sys.exit(7)\n"
            "if os.environ.get('FAIL_AUTH') and Path(sys.argv[0]).name == 'sudo' and args == ['-v']:\n"
            "    sys.exit(9)\n"
            "if os.environ.get('FAIL_STEP') and args and args[0] == os.environ['FAIL_STEP']:\n"
            "    sys.exit(8)\n"
        )
        executable.chmod(0o755)
    env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}", "COMMAND_LOG": str(log)}

    def invoke(*args, fail=False, fail_auth=False, fail_step=None):
        log.unlink(missing_ok=True)
        invocation_env = {**env, **({"FAIL_BUILD": "1"} if fail else {}),
                          **({"FAIL_AUTH": "1"} if fail_auth else {}),
                          **({"FAIL_STEP": fail_step} if fail_step else {})}
        result = subprocess.run(["bash", str(script), *args], env=invocation_env,
                                text=True, capture_output=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return invoke


def test_install_only_after_full_build(wrapper):
    result, calls = wrapper("--install", "--parallel", "3")
    assert result.returncode == 0, result.stderr
    assert calls[-3] == ["cmake", "--build", "build", "--parallel", "3"]
    assert calls[-2] == ["sudo", "-v"]
    assert calls[-1][:3] == ["sudo", "--", "python3"]
    assert calls[-1][3].endswith("/scripts/install.py")
    assert calls[-1][4] == "-model"
    assert calls[-1][5].endswith("/state/model.json")
    assert "install-prefix=/usr" in calls[0]
    assert calls[1] == ["python3", "scripts/check_sources.py", "-model", "state/model.json"]
    assert calls[2] == ["python3", "scripts/validate.py", "-model", "state/model.json"]
    assert "--staging-root" in calls[3]
    assert all("--install" not in call for call in calls)


def test_failed_build_never_deploys(wrapper):
    result, calls = wrapper("--install", fail=True)
    assert result.returncode == 7
    assert not any("scripts/install.py" in call for call in calls)
    assert not any(call[0] == "sudo" for call in calls)


def test_failed_authentication_never_installs(wrapper):
    result, calls = wrapper("--install", fail_auth=True)
    assert result.returncode == 9
    assert calls[-2] == ["cmake", "--build", "build"]
    assert calls[-1] == ["sudo", "-v"]
    assert not any(any(arg.endswith("scripts/install.py") for arg in call) for call in calls)


def test_default_does_not_deploy_or_change_prefix(wrapper):
    result, calls = wrapper("--verbose")
    assert result.returncode == 0
    assert calls[-1] == ["cmake", "--build", "build", "--verbose"]
    assert not any(call[0] == "sudo" for call in calls)
    assert not any("--staging-root" in call or "scripts/install.py" in call for call in calls)
    assert not any(arg.startswith("install-prefix=") for call in calls for arg in call)
    assert not any("scripts/fetch.py" in call for call in calls)


def test_source_failure_stops_before_cmake_or_sudo(wrapper):
    step = "scripts/check_sources.py"
    result, calls = wrapper("--install", fail_step=step)
    assert result.returncode == 8
    assert calls[-1] == ["python3", step, "-model", "state/model.json"]
    assert all(call[0] == "python3" for call in calls)
    assert not any("scripts/validate.py" in call or "scripts/install.py" in call for call in calls)


def test_explicit_prefix_and_defines(wrapper):
    result, calls = wrapper("--install", "--install-prefix", "/opt/sonic desktop", "-solution-define", "kf6_version=v1")
    assert result.returncode == 0
    assert "install-prefix=/opt/sonic desktop" in calls[0]
    assert "kf6_version=v1" in calls[0]
    assert "kf6_version=v1" not in calls[1]
    assert "kf6_version=v1" not in calls[-2]


@pytest.mark.parametrize("args", [
    ["--install-prefix"], ["--install-prefix", "/usr"],
    ["--install", "--install-prefix", "relative"], ["--parallel", "no"],
    ["--install", "--target", "one-package"], ["--unknown"], ["--fetch"],
])
def test_bad_arguments_do_not_start_pipeline(wrapper, args):
    result, calls = wrapper(*args)
    assert result.returncode == 2
    assert not calls


def test_help_has_no_side_effects(wrapper):
    result, calls = wrapper("--help")
    assert result.returncode == 0
    assert "--install" in result.stdout
    assert not calls
