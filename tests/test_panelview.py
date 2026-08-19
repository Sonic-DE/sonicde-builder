#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
"""Regression checks for PanelView screen-lifetime handling."""
import re
import unittest
from pathlib import Path


PANELVIEW = (
    Path(__file__).parent.parent
    / "src"
    / "sonicde"
    / "sonic-workspace"
    / "shell"
    / "panelview.cpp"
)


class TestPanelViewScreenGuard(unittest.TestCase):

    def test_preferred_size_guards_destroyed_screen(self):
        """preferredSize must not dereference a cleared QPointer<QScreen>."""
        source = PANELVIEW.read_text(encoding="utf-8")
        match = re.search(
            r"QSize\s+PanelView::preferredSize\(\)\s+const\s*\{(?P<body>.*?)\n\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "PanelView::preferredSize() not found")
        body = match.group("body")
        self.assertRegex(
            body,
            r"if\s*\(\s*!m_initCompleted\s*\|\|\s*!m_screenToFollow\s*\)\s*\{\s*return\s*\{\}\s*;",
            "preferredSize() must return before using a null screen",
        )
        self.assertLess(
            body.index("!m_screenToFollow"),
            body.index("m_screenToFollow->"),
            "screen guard must precede every screen dereference",
        )


if __name__ == "__main__":
    unittest.main()
