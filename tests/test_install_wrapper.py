# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the deployment-only install wrapper offline: checks before sudo, nothing built."""
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
    script = tmp_path / "install"
    shutil.copy2(ROOT / "install", script)
    (tmp_path / "state").mkdir()
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
            "if os.environ.get('FAIL_CHECK') and '--check' in args:\n"
            "    sys.exit(8)\n"
            "if os.environ.get('FAIL_AUTH') and Path(sys.argv[0]).name == 'sudo' and args == ['-v']:\n"
            "    sys.exit(9)\n"
        )
        executable.chmod(0o755)
    env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}", "COMMAND_LOG": str(log)}

    def invoke(*args, model="state/model.json", fail_check=False, fail_auth=False):
        log.unlink(missing_ok=True)
        if model:
            (tmp_path / model).write_text("{}")
        invocation_env = {**env, **({"FAIL_CHECK": "1"} if fail_check else {}),
                          **({"FAIL_AUTH": "1"} if fail_auth else {})}
        result = subprocess.run(["bash", str(script), *args], env=invocation_env,
                                text=True, capture_output=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    invoke.root = tmp_path
    return invoke


def test_checks_then_installs_without_building(wrapper):
    result, calls = wrapper()
    assert result.returncode == 0, result.stderr
    model = str(wrapper.root / "state/model.json")
    assert calls[0] == ["python3", "scripts/install.py", "-model", model, "--check"]
    assert calls[1] == ["sudo", "-v"]
    assert calls[2] == ["sudo", "--", "python3", str(wrapper.root / "scripts/install.py"), "-model", model]
    assert len(calls) == 3
    assert not any(call[0] == "cmake" for call in calls)


def test_other_model(wrapper):
    result, calls = wrapper("--model", "state/build-model.json", model="state/build-model.json")
    assert result.returncode == 0, result.stderr
    assert calls[-1][-1] == str(wrapper.root / "state/build-model.json")


def test_failed_check_never_authenticates(wrapper):
    result, calls = wrapper(fail_check=True)
    assert result.returncode == 8
    assert not any(call[0] == "sudo" for call in calls)


def test_failed_authentication_never_installs(wrapper):
    result, calls = wrapper(fail_auth=True)
    assert result.returncode == 9
    assert calls[-1] == ["sudo", "-v"]


def test_missing_model_starts_nothing(wrapper):
    result, calls = wrapper("--model", "state/none.json", model=None)
    assert result.returncode == 2
    assert not calls


@pytest.mark.parametrize("args", [["--model"], ["--install"], ["sonic-terminal"]])
def test_bad_arguments_start_nothing(wrapper, args):
    result, calls = wrapper(*args)
    assert result.returncode == 2
    assert not calls


def test_help_has_no_side_effects(wrapper):
    result, calls = wrapper("--help")
    assert result.returncode == 0
    assert "--model" in result.stdout
    assert not calls
