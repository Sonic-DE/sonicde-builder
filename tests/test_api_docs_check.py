#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for api_docs_check.py: each rule passes on a consistent reference and fails, naming the
problem, when that reference is broken. Everything is checked offline."""
import io
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import api_docs_check  # noqa: E402

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<INDEX project="KDemo">
<namespace name="" status="active" access="public" module="kdemo">
<module name="KDemo" href="kdemo-module.html" status="active" documented="true"/>
<class name="KThing" href="kthing.html" status="active" access="public" documented="true" module="KDemo">
<function name="start" href="kthing.html#start" status="active" documented="true"/>
</class>
<class name="KOld" href="kold.html" status="deprecated" access="public" documented="true" module="KDemo"/>
<class name="KHidden" href="khidden.html" status="internal" access="public" documented="true" module="KDemo"/>
</namespace>
</INDEX>
"""

QT_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<INDEX project="QtCore">
<namespace name="" status="active">
<class name="QObject" href="qobject.html" status="active" documented="true">
<function name="deleteLater" href="qobject.html#deleteLater" status="active"/>
</class>
</namespace>
</INDEX>
"""

PAGES = {
    "toc.yaml": 'rootPage: index\nsections:\n  "Overview":\n    pages:\n      index: "All Modules"\n'
                '  "KDemo":\n    pages:\n      kdemo/kdemo-module: "KDemo"\n      kdemo/kthing: "KThing"\n'
                '      kdemo/kold: "KOld"\n',
    "index.md": "# All Modules\n\nGuides are in the [documentation](/docs).\n\n- [KDemo](kdemo/kdemo-module.md)\n",
    "kdemo/kdemo-module.md": "# KDemo C++ Classes\n\n## Classes {#classes}\n\n- [KThing](kthing.md): A thing.\n",
    "kdemo/kthing.md": "# KThing Class\n\n- **Inherits:** [QObject](https://doc.qt.io/qt-6/qobject.html)\n\n"
                       "[All members](kthing-members.md)\n\n## Public Functions\n\n- [void start()](#start)\n\n"
                       "## Detailed Description {#details}\n\nSee [deleteLater](https://doc.qt.io/qt-6/qobject.html#deleteLater)"
                       " and [the qobject docs](https://doc.qt.io/qt-6/qobject.html#details).\n\n```cpp\nauto f = [](int x) {};\n```\n\n"
                       "Code `[a](b)` isn't a link. [Elsewhere](https://example.org/x).\n\n"
                       "## Member Function Documentation\n\n### void KThing::start() {#start}\n\nStarts.\n",
    "kdemo/kthing-members.md": "# All members\n\n- [start()](kthing.md#start)\n",
    "kdemo/kold.md": "# KOld Class\n\n[Back](../index.md)\n",
    "kdemo/kold-members.md": "# All members\n",
}


class CheckTest(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.reference = self.root / "api"
        for name, text in PAGES.items():
            path = self.reference / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.work = self.root / "work"
        (self.work / "index" / "kdemo").mkdir(parents=True)
        (self.work / "index" / "kdemo" / "kdemo.index").write_text(INDEX)
        (self.work / "logs").mkdir()
        self.qt = self.root / "qt" / "qtcore"
        self.qt.mkdir(parents=True)
        (self.qt / "qtcore.index").write_text(QT_INDEX)
        (self.qt / "qobject.html").write_text('<h2 id="details">Detailed Description</h2>')

    def run_check(self) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), unittest.mock.patch("sys.stderr", io.StringIO()):
            code = api_docs_check.main(["--reference", str(self.reference), "--work", str(self.work),
                                        "--qt-docs", str(self.qt.parent)])
        return code, out.getvalue()

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.reference / name
        self.assertIn(old, path.read_text())
        path.write_text(path.read_text().replace(old, new))

    def assert_problem(self, text: str) -> None:
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn(text, out)

    def test_a_consistent_reference_passes_without_fetching(self):
        with unittest.mock.patch("urllib.request.urlopen", side_effect=AssertionError("fetched")):
            code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("1 modules, 2 classes, 1 documented members", out)
        self.assertIn("3 Qt links, 1 site pages (/docs), 1 other external pages (not fetched)", out)
        self.assertIn("No problems", out)

    def test_internal_links_need_their_file_and_anchor(self):
        self.edit("kdemo/kthing-members.md", "kthing.md#start", "kthing.md#stop")
        self.assert_problem("kdemo/kthing-members.md: no anchor #stop in kthing.md")
        self.edit("kdemo/kthing-members.md", "kthing.md#stop", "kthing2.md#start")
        self.assert_problem("kdemo/kthing-members.md: no such file: kthing2.md#start")
        self.edit("kdemo/kthing.md", "(#start)", "(#begin)")
        self.assert_problem("kdemo/kthing.md: no anchor #begin")

    def test_links_may_not_leave_the_reference(self):
        self.edit("kdemo/kold.md", "../index.md", "../../outside.md")
        (self.root / "outside.md").write_text("# Outside\n")
        self.assert_problem("kdemo/kold.md: no such file: ../../outside.md")

    def test_every_toc_page_exists(self):
        (self.reference / "kdemo" / "kold.md").unlink()
        self.assert_problem("toc.yaml: no page kdemo/kold.md")

    def test_qt_links_need_a_page_and_anchor_of_the_installed_documentation(self):
        self.edit("kdemo/kthing.md", "qobject.html#deleteLater", "qobject.html#destroyLater")
        self.assert_problem("not in the Qt documentation: https://doc.qt.io/qt-6/qobject.html#destroyLater")
        self.edit("kdemo/kthing.md", "qobject.html#destroyLater", "qobjekt.html")
        self.assert_problem("not in the Qt documentation: https://doc.qt.io/qt-6/qobjekt.html")

    def test_qt_anchors_come_from_the_index_or_the_page(self):
        (self.qt / "qobject.html").unlink()
        self.assert_problem("not in the Qt documentation: https://doc.qt.io/qt-6/qobject.html#details")

    def test_every_documented_class_has_its_pages_and_listings(self):
        (self.reference / "kdemo" / "kthing-members.md").unlink()
        self.edit("kdemo/kthing.md", "[All members](kthing-members.md)", "")
        self.assert_problem("kdemo: KThing has no all-members page kdemo/kthing-members.md")
        self.edit("kdemo/kdemo-module.md", "[KThing](kthing.md)", "KThing")
        self.assert_problem("kdemo: KThing isn't listed in its module page")
        self.edit("toc.yaml", '      kdemo/kthing: "KThing"\n', "")
        self.assert_problem("kdemo: KThing isn't in toc.yaml")
        (self.reference / "kdemo" / "kthing.md").unlink()
        self.assert_problem("kdemo: KThing has no page kdemo/kthing.md")

    def test_a_module_index_without_pages_fails(self):
        (self.work / "index" / "kgone").mkdir()
        self.assert_problem("kgone: has an index but no pages")

    def test_undocumented_and_left_out_symbols_are_listed_but_do_not_fail(self):
        (self.work / "logs" / "generate-kdemo.log").write_text(
            "kthing.h:3: [KDemo] (qdoc) warning: Undocumented property 'KThing::size'\nother line\n")
        (self.work / "logs" / "convert-kdemo.log").write_text(
            "(II) The base class \"kother/kbase\" of \"KThing\" isn't in the reference yet; its members are left out\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("2 undocumented or left-out symbols", out)
        self.assertIn("Undocumented property 'KThing::size'", out)
        self.assertNotIn("other line", out)


if __name__ == "__main__":
    unittest.main()
