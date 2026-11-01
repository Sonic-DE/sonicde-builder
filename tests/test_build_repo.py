# SPDX-License-Identifier: GPL-2.0-only
"""Exercise single-repository build routing and its deployment barrier, offline."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL = "state/build-model.json"


@pytest.fixture
def wrapper(tmp_path):
    script = tmp_path / "build-repo"
    shutil.copy2(ROOT / "build-repo", script)
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
            "if os.environ.get('FAIL_STEP') and args and args[0] == os.environ['FAIL_STEP']:\n"
            "    sys.exit(8)\n"
        )
        executable.chmod(0o755)
    env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}", "COMMAND_LOG": str(log),
           "SONICDE_BUILD_JOBS": "2"}

    def invoke(*args, fail=False, fail_step=None):
        log.unlink(missing_ok=True)
        invocation_env = {**env, **({"FAIL_BUILD": "1"} if fail else {}),
                          **({"FAIL_STEP": fail_step} if fail_step else {})}
        result = subprocess.run(["bash", str(script), *args], env=invocation_env,
                                text=True, capture_output=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return invoke


def test_selected_model_is_built_and_installed_after_build(wrapper):
    result, calls = wrapper("sonic-firewall", "--install", "--parallel", "3")
    assert result.returncode == 0, result.stderr
    config = calls[0]
    assert config[:2] == ["python3", "scripts/config.py"]
    assert config[config.index("-out") + 1] == MODEL
    assert config[config.index("-build") + 1] == "sonic-firewall"
    assert "install-prefix=/usr" in config
    assert calls[1] == ["python3", "scripts/check_sources.py", "-model", MODEL]
    assert calls[2] == ["python3", "scripts/validate.py", "-model", MODEL]
    generate = calls[3]
    assert generate[generate.index("-model") + 1] == MODEL
    assert generate[generate.index("--jobs") + 1] == "3"
    assert generate[generate.index("--dot-out") + 1] == "state/build-depgraph.dot"
    assert "--staging-root" in generate
    assert calls[-3] == ["cmake", "--build", "build", "--parallel", "1"]
    assert calls[-2] == ["sudo", "-v"]
    assert calls[-1][:3] == ["sudo", "--", "python3"]
    assert calls[-1][3].endswith("/scripts/install.py")
    assert calls[-1][5].endswith("/" + MODEL)


def test_full_solution_model_is_never_written(wrapper):
    result, calls = wrapper("sonicde/sonic-firewall", "--verbose")
    assert result.returncode == 0, result.stderr
    assert not any("state/model.json" in arg for call in calls for arg in call)
    assert not any(call[0] == "sudo" for call in calls)
    assert calls[-1] == ["cmake", "--build", "build", "--parallel", "1", "--verbose"]

def test_default_builds_staged_for_usr_and_prefix_overrides(wrapper):
    result, calls = wrapper("sonic-firewall")
    assert result.returncode == 0, result.stderr
    assert "install-prefix=/usr" in calls[0]
    assert "--staging-root" in calls[3]
    result, calls = wrapper("sonic-firewall", "--prefix", "/opt/sonicde")
    assert result.returncode == 0, result.stderr
    assert "install-prefix=/opt/sonicde" in calls[0]
    assert not any(call[0] == "sudo" for call in calls)


def test_failed_build_never_deploys(wrapper):
    result, calls = wrapper("sonic-firewall", "--install", fail=True)
    assert result.returncode == 7
    assert not any(call[0] == "sudo" for call in calls)


def test_resolution_failure_stops_pipeline(wrapper):
    result, calls = wrapper("sonic-firewall", "--install", fail_step="scripts/config.py")
    assert result.returncode == 8
    assert len(calls) == 1


@pytest.mark.parametrize("args", [
    [], ["--install"], ["-j", "2"], ["bad name"], ["../escape"],
    ["sonic-firewall", "--prefix", "relative"], ["sonic-firewall", "--parallel", "no"],
    ["sonic-firewall", "--unknown"], ["sonic-firewall", "--install", "--install-prefix", "relative"],
])
def test_bad_arguments_do_not_start_pipeline(wrapper, args):
    result, calls = wrapper(*args)
    assert result.returncode == 2
    assert not calls


def test_help_has_no_side_effects(wrapper):
    result, calls = wrapper("--help")
    assert result.returncode == 0
    assert "REPOSITORY" in result.stdout
    assert not calls
