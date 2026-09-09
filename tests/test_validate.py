# SPDX-License-Identifier: GPL-2.0-only
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate import validate_cmake_packages, validate_commands, validate_headers, validate_python_modules, validate_system_packages


def test_python_module_probe_accepts_importable_module(capsys):
    assert validate_python_modules("fixture", ["json.encoder"]) == 0
    assert "Python module: json.encoder" in capsys.readouterr().out


def test_python_module_probe_rejects_missing_backend(capsys):
    assert validate_python_modules("fixture", ["sonicde_missing_python_backend_12345"]) == 1
    assert "missing Python module" in capsys.readouterr().err


def test_command_probe_accepts_and_rejects_executables(capsys):
    assert validate_commands("fixture", ["cmake"]) == 0
    assert validate_commands("fixture", ["sonicde-missing-command-12345"]) == 1
    output = capsys.readouterr()
    assert "executable: cmake:" in output.out
    assert "missing executable" in output.err


def test_header_probe_accepts_and_rejects_headers(tmp_path, monkeypatch, capsys):
    include = tmp_path / "include"
    (include / "boost").mkdir(parents=True)
    (include / "boost/version.hpp").write_text("// fixture\n")
    monkeypatch.setenv("CPATH", str(include))
    assert validate_headers("fixture", ["boost/version.hpp"]) == 0
    assert validate_headers("fixture", ["missing/header.hpp"]) == 1
    output = capsys.readouterr()
    assert "header: boost/version.hpp:" in output.out
    assert "missing header" in output.err


def test_cmake_package_probe_accepts_and_rejects_configs(capsys):
    assert validate_cmake_packages("fixture", ["Qt6Core"]) == 0
    assert validate_cmake_packages("fixture", ["SonicDEMissingCMakePackage12345"]) == 1
    output = capsys.readouterr()
    assert "CMake package: Qt6Core" in output.out
    assert "missing CMake package" in output.err


def test_system_validation_checks_pkg_config_and_python(monkeypatch):
    probed = []
    monkeypatch.setattr("validate.validate_pkg_config", lambda name, modules: probed.append(("pkg", name, modules)) or 0)
    monkeypatch.setattr("validate.validate_python_modules", lambda name, modules: probed.append(("python", name, modules)) or 0)
    monkeypatch.setattr("validate.validate_commands", lambda name, commands: probed.append(("command", name, commands)) or 0)
    monkeypatch.setattr("validate.validate_headers", lambda name, headers: probed.append(("header", name, headers)) or 0)
    monkeypatch.setattr("validate.validate_cmake_packages", lambda name, packages: probed.append(("cmake", name, packages)) or 0)
    model = {"packages": {"os-installed/python": {"type": "system", "pkg_config": [],
                                                    "python_modules": ["setuptools.build_meta"], "commands": ["sassc"],
                                                    "headers": ["boost/version.hpp"], "cmake_packages": ["Qt6Keychain"]},
                          "source/package": {"type": "", "python_modules": ["ignored"]}}}
    assert validate_system_packages(model) == 0
    assert probed == [("pkg", "os-installed/python", []),
                      ("python", "os-installed/python", ["setuptools.build_meta"]),
                      ("command", "os-installed/python", ["sassc"]),
                      ("header", "os-installed/python", ["boost/version.hpp"]),
                      ("cmake", "os-installed/python", ["Qt6Keychain"])]
