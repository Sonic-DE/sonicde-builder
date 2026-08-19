#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Regression checks for the kwin_x11 -> sonic-win executable and service rename.

These tests assert that the strict rename was applied to every runtime
executable target, service-unit identity, unit dependency, launch path,
crash-fallback string, crash-mapping predicate, and bug-product mapping --
while stable compatibility identifiers (DBus names, translation domains,
plugin metadata, CMake package names) are intentionally left unchanged.
"""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "sonicde"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


class TestSonicWinExecutable(unittest.TestCase):
    """The CMake executable target and install output must be sonic-win."""

    def test_target_renamed(self):
        source = _read("sonic-win/src/CMakeLists.txt")
        self.assertIn("add_executable(sonic-win main_x11.cpp)", source)
        self.assertNotIn("add_executable(kwin_x11", source)

    def test_install_target_renamed(self):
        source = _read("sonic-win/src/CMakeLists.txt")
        self.assertRegex(source, r"install\(TARGETS\s+sonic-win\s")
        self.assertNotRegex(source, r"install\(TARGETS\s+kwin_x11\s")

    def test_translation_domain_preserved(self):
        source = _read("sonic-win/src/CMakeLists.txt")
        self.assertIn('TRANSLATION_DOMAIN=\\"kwin_x11\\"', source)

    def test_crash_fallback_strings(self):
        source = _read("sonic-win/src/main_x11.cpp")
        self.assertIn('addWM(QStringLiteral("sonic-win"))', source)
        self.assertIn('QString cmd = QStringLiteral("sonic-win")', source)
        self.assertNotIn('addWM(QStringLiteral("kwin_x11"))', source)
        self.assertNotIn('QString cmd = QStringLiteral("kwin_x11")', source)


class TestSonicWinService(unittest.TestCase):
    """The normal-session systemd unit must be sonic-win.service."""

    def test_service_file_renamed(self):
        self.assertTrue((SRC / "sonic-win" / "sonic-win.service.in").exists())
        self.assertFalse((SRC / "sonic-win" / "plasma-kwin_x11.service.in").exists())

    def test_cmake_input_updated(self):
        source = _read("sonic-win/CMakeLists.txt")
        self.assertIn("INPUT sonic-win.service.in", source)
        self.assertNotIn("INPUT plasma-kwin_x11.service.in", source)

    def test_execstart_uses_sonic_win(self):
        source = _read("sonic-win/sonic-win.service.in")
        self.assertIn("ExecStart=@CMAKE_INSTALL_FULL_BINDIR@/sonic-win --replace", source)
        self.assertNotIn("kwin_x11", source)

    def test_busname_preserved(self):
        source = _read("sonic-win/sonic-win.service.in")
        self.assertIn("BusName=org.kde.KWin", source)


class TestSonicWorkspace(unittest.TestCase):
    """Normal-session consumers must reference sonic-win and sonic-win.service."""

    def test_kwin_bin_default(self):
        source = _read("sonic-workspace/ConfigureChecks.cmake")
        self.assertIn('set(KWIN_BIN "sonic-win"', source)
        self.assertNotIn('set(KWIN_BIN "kwin_x11"', source)

    def test_x11_target_wants(self):
        source = _read("sonic-workspace/startkde/systemd/plasma-workspace-x11.target")
        self.assertIn("Wants=sonic-win.service", source)
        self.assertNotIn("plasma-kwin_x11.service", source)

    def test_ksmserver_after(self):
        source = _read("sonic-workspace/ksmserver/plasma-ksmserver.service.in")
        self.assertIn("After=sonic-win.service", source)
        self.assertNotIn("plasma-kwin_x11.service", source)


class TestSonicLoginManager(unittest.TestCase):
    """The login-manager unit must be soniclogin-sonic-win.service."""

    def test_service_file_renamed(self):
        path = "sonic-login-manager/src/frontend/startkde/soniclogin-sonic-win.service.in"
        old = "sonic-login-manager/src/frontend/startkde/soniclogin-kwin_x11.service.in"
        self.assertTrue((SRC / path).exists())
        self.assertFalse((SRC / old).exists())

    def test_cmake_input_updated(self):
        source = _read("sonic-login-manager/src/frontend/startkde/CMakeLists.txt")
        self.assertIn("INPUT soniclogin-sonic-win.service.in", source)
        self.assertNotIn("INPUT soniclogin-kwin_x11.service.in", source)

    def test_execstart_uses_sonic_win(self):
        source = _read(
            "sonic-login-manager/src/frontend/startkde/soniclogin-sonic-win.service.in"
        )
        self.assertIn("ExecStart=@CMAKE_INSTALL_FULL_BINDIR@/sonic-win --replace", source)
        self.assertNotIn("kwin_x11", source)

    def test_x11_target_deps(self):
        source = _read("sonic-login-manager/src/frontend/startkde/soniclogin-x11.target")
        self.assertIn("Requires=soniclogin-sonic-win.service", source)
        self.assertIn("BindsTo=soniclogin-sonic-win.service", source)
        self.assertNotIn("soniclogin-kwin_x11.service", source)

    def test_apply_kscreen_deps(self):
        source = _read(
            "sonic-login-manager/src/frontend/startkde/soniclogin-apply-kscreen.service.in"
        )
        self.assertIn("After=soniclogin-sonic-win.service", source)
        self.assertIn("Requires=soniclogin-sonic-win.service", source)
        self.assertNotIn("soniclogin-kwin_x11.service", source)

    def test_greeter_after(self):
        source = _read(
            "sonic-login-manager/src/frontend/greeter/soniclogin.service.in"
        )
        self.assertIn("After=soniclogin-sonic-win.service", source)
        self.assertNotIn("soniclogin-kwin_x11.service", source)

    def test_wallpaper_after(self):
        source = _read(
            "sonic-login-manager/src/frontend/wallpaper/soniclogin-wallpaper.service.in"
        )
        self.assertIn("After=soniclogin-sonic-win.service", source)
        self.assertNotIn("soniclogin-kwin_x11.service", source)

    def test_direct_launch_path(self):
        source = _read(
            "sonic-login-manager/src/frontend/startkde/start-soniclogin-x11.cpp"
        )
        self.assertIn('BIN_INSTALL_DIR "/sonic-win"', source)
        self.assertNotIn('BIN_INSTALL_DIR "/kwin_x11"', source)

    def test_stopunit_target(self):
        source = _read(
            "sonic-login-manager/src/frontend/startkde/start-soniclogin-x11.cpp"
        )
        self.assertIn(
            'QStringLiteral("soniclogin-sonic-win.service")', source
        )
        self.assertNotIn(
            'QStringLiteral("soniclogin-kwin_x11.service")', source
        )


class TestSonicDrRobotnik(unittest.TestCase):
    """Crash-report consumers must recognize sonic-win."""

    def test_coredump_exe_match(self):
        source = _read("sonic-dr-robotnik/src/coredump/launcher/main.cpp")
        self.assertIn('dump.exe.endsWith("/sonic-win"_L1)', source)
        self.assertNotIn('dump.exe.endsWith("/kwin_x11"_L1)', source)

    def test_mappings_entry(self):
        source = _read("sonic-dr-robotnik/src/data/mappings")
        self.assertIn("sonic-win=kwin|general", source)
        self.assertNotIn("kwin_x11=kwin|general", source)


if __name__ == "__main__":
    unittest.main()
