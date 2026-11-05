#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Tests for api_docs.py: reading ECM's QDoc commands, the two QDoc passes over shared index and
output directories, failure handling, and replacing the published folder. QDoc and the converter
are stand-in scripts that record how they were called."""
import os
import re
import stat
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import api_docs  # noqa: E402

# Shaped like `ninja -t commands prepare_docs_KF6ConfigCore` in a build configured by ECMGenerateQDoc.
ECM_COMMAND = (
    "cd /b/sonic-frameworks-settings/src/core && /usr/bin/cmake -E env QT_VERSION=6.31.0 QT_VER=6.31 "
    "QT_VERSION_TAG=6310 BUILDDIR=/b/sonic-frameworks-settings/src/core /usr/lib64/qt6/bin/qdoc "
    "-outputdir /b/sonic-frameworks-settings/.doc/kconfigcore /s/sonic-frameworks-settings/src/core/kconfigcore.qdocconf "
    "-prepare --indexdir /b/sonic-frameworks-settings/.doc --indexdir /usr/share/qt6-doc -no-link-errors "
    "-installdir /b/sonic-frameworks-settings/.doc @/b/sonic-frameworks-settings/.doc/KF6ConfigCore_Debug_includes.txt"
)


def write_script(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TemplatesTest(unittest.TestCase):
    def test_overlay_keeps_qt_files_and_replaces_module_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            qt_docs = Path(tmp) / "qt"
            (qt_docs / "global" / "includes").mkdir(parents=True)
            (qt_docs / "global" / "defaults.qdocconf").write_text("qt\n")
            (qt_docs / "global" / "includes" / "other.qdocinc").write_text("qt\n")
            (qt_docs / "global" / "includes" / api_docs.MODULE_USE.name).write_text("qt\n")
            target = Path(tmp) / "templates"
            for _ in range(2):  # a later run replaces the earlier overlay
                api_docs.overlay_templates(qt_docs, target)
            self.assertEqual((target / "global" / "defaults.qdocconf").read_text(), "qt\n")
            self.assertEqual((target / "global" / "includes" / "other.qdocinc").read_text(), "qt\n")
            self.assertEqual((target / "global" / "includes" / api_docs.MODULE_USE.name).read_text(),
                             api_docs.MODULE_USE.read_text())
            self.assertEqual((qt_docs / "global" / "includes" / api_docs.MODULE_USE.name).read_text(), "qt\n")

    def test_every_module_use_snippet_in_the_sources_is_in_the_template(self):
        template = api_docs.MODULE_USE.read_text()
        sources = Path(api_docs.ROOT) / "src" / "sonicde"
        names = set()
        for qdoc in sources.glob("*/src/**/*.qdoc") if sources.is_dir() else []:
            names.update(re.findall(r"\\include \{module-use\.qdocinc\} \{([^}]+)\}", qdoc.read_text(errors="replace")))
        for name in names:
            self.assertIn(f"//! [{name}]", template)


class ParseCommandTest(unittest.TestCase):

    def test_reads_directory_environment_qdocconf_and_includes(self):
        module = api_docs.parse_command("sonic-frameworks-settings", "KF6ConfigCore", ECM_COMMAND)
        self.assertEqual(module.cwd, Path("/b/sonic-frameworks-settings/src/core"))
        self.assertEqual(module.env, {"QT_VERSION": "6.31.0", "QT_VER": "6.31", "QT_VERSION_TAG": "6310",
                                      "BUILDDIR": "/b/sonic-frameworks-settings/src/core"})
        self.assertEqual(module.qdoc, "/usr/lib64/qt6/bin/qdoc")
        self.assertEqual(module.name, "kconfigcore")
        self.assertEqual(module.includes, "/b/sonic-frameworks-settings/.doc/KF6ConfigCore_Debug_includes.txt")
        # Qt's documentation directory comes from ECM; the build's own .doc output doesn't.
        self.assertEqual(module.index_dirs, ["/usr/share/qt6-doc"])
        self.assertEqual(api_docs.qt_docs_dir([module]), Path("/usr/share/qt6-doc"))

    def test_rejects_commands_it_cannot_read(self):
        for command in ("qdoc x.qdocconf", "cd /x && qdoc -prepare @inc.txt", "cd /x && qdoc a.qdocconf"):
            with self.assertRaises(ValueError):
                api_docs.parse_command("repo", "Target", command)


class QDocCommandTest(unittest.TestCase):

    def test_passes_share_indexes_and_differ_only_in_format_and_output(self):
        module = api_docs.parse_command("sonic-frameworks-settings", "KF6ConfigCore", ECM_COMMAND)
        work = Path("/w")
        prepare = api_docs.qdoc_command(module, "prepare", work)
        generate = api_docs.qdoc_command(module, "generate", work)

        def option(command, name):
            return command[command.index(name) + 1]

        # Only QDoc's HTML output writes .index files; the published pages come from DocBook.
        self.assertEqual(option(prepare, "--outputformat"), "HTML")
        self.assertEqual(option(generate, "--outputformat"), "DocBook")
        self.assertEqual(option(prepare, "-outputdir"), "/w/index/kconfigcore")
        self.assertEqual(option(generate, "-outputdir"), "/w/docbook/kconfigcore")
        for command in (prepare, generate):
            # Every module's index is found in the shared directory, Qt's where ECM found them.
            indexdirs = [command[i + 1] for i, word in enumerate(command) if word == "--indexdir"]
            self.assertEqual(indexdirs, ["/w/index", *module.index_dirs])
            self.assertIn(str(module.qdocconf), command)
            self.assertIn(f"@{module.includes}", command)
            # Nothing is written into the library's own build directory.
            self.assertFalse(any("/b/" in word for word in command if not word.startswith("@")))


class PassesTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.calls = self.root / "calls.txt"
        # Stand-in qdoc: records "<phase> <module> <format> KDE_DOCS=..." and fails for "broken".
        self.qdoc = write_script(self.root / "qdoc", f"""
conf=$(basename "$1" .qdocconf)
echo "$2 $conf $4 KDE_DOCS=$KDE_DOCS BUILDDIR=$BUILDDIR" >> {self.calls}
[ "$conf" = broken ] && exit 3
mkdir -p "$6"
""")

    def tearDown(self):
        self.tmpdir.cleanup()

    def module(self, name: str) -> api_docs.Module:
        cwd = self.root / name
        cwd.mkdir(exist_ok=True)
        return api_docs.Module("repo", name, cwd, {"BUILDDIR": f"/build/{name}"}, self.qdoc,
                               cwd / f"{name}.qdocconf", str(cwd / "includes.txt"))

    def test_every_prepare_runs_before_any_generate_and_failures_drop_out(self):
        modules = [self.module(name) for name in ("first", "broken", "last")]
        result = api_docs.Result()
        work = self.root / "work"
        for phase in ("prepare", "generate"):
            api_docs.run_pass(modules, phase, work, Path("/kde-docs"), 2, result)

        calls = self.calls.read_text().splitlines()
        phases = [call.split()[0] for call in calls]
        self.assertEqual(phases, sorted(phases, key=lambda p: p != "--prepare"))
        # The failing module is reported with its log and isn't generated; the others are.
        self.assertEqual(list(result.failed), ["broken"])
        self.assertIn("prepare-broken.log", result.failed["broken"])
        generated = {call.split()[1] for call in calls if call.startswith("--generate")}
        self.assertEqual(generated, {"first", "last"})
        # Each module runs with its own ECM environment and the chosen QDoc templates.
        self.assertIn("--prepare first HTML KDE_DOCS=/kde-docs BUILDDIR=/build/first", calls)
        self.assertTrue((work / "logs" / "generate-last.log").is_file())

    def test_conversion_failures_are_reported_and_the_rest_converted(self):
        modules = [self.module(name) for name in ("first", "broken", "last")]
        converted = self.root / "converted.txt"
        converter = write_script(self.root / "docbook2markdown6", f"""
echo "$2" >> {converted}
[ "$2" = broken ] && exit 1
mkdir -p "$6/$2" && echo page > "$6/$2/page.md"
""")
        result = api_docs.Result()
        markdown = api_docs.convert(modules, self.root / "work", converter, Path("/qt-docs"), result)
        self.assertEqual(converted.read_text().split(), ["first", "broken", "last"])
        self.assertEqual(result.ok, ["first", "last"])
        self.assertEqual(list(result.failed), ["broken"])
        self.assertEqual(sorted(p.parent.name for p in markdown.rglob("*.md")), ["first", "last"])

    def test_the_prepare_index_is_passed_whatever_its_name(self):
        modules = [self.module(name) for name in ("first", "noindex")]
        work = self.root / "work"
        index = work / "index" / "first" / "firstproject.index"
        index.parent.mkdir(parents=True)
        index.write_text("<INDEX/>\n")
        arguments = self.root / "arguments.txt"
        converter = write_script(self.root / "docbook2markdown6", f'echo "$*" >> {arguments}\n')
        api_docs.convert(modules, work, converter, Path("/qt-docs"), api_docs.Result())
        first, noindex = arguments.read_text().splitlines()
        self.assertIn(f"--index {index} ", first)
        self.assertNotIn("--index", noindex)

    def test_modules_failed_in_qdoc_are_not_converted(self):
        modules = [self.module("broken")]
        converter = write_script(self.root / "docbook2markdown6", "exit 0\n")
        result = api_docs.Result()
        result.failed["broken"] = "qdoc --prepare exited 3"
        api_docs.convert(modules, self.root / "work", converter, Path("/qt-docs"), result)
        self.assertEqual(result.ok, [])
        self.assertEqual(result.failed["broken"], "qdoc --prepare exited 3")


class IncrementalTest(unittest.TestCase):
    """main() end to end with stand-in qdoc and converter: the index a module's prepare pass writes
    holds its .qdocconf's "api" lines, so editing them changes the index and anything else doesn't."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.calls = self.root / "calls.txt"
        self.qdoc = write_script(self.root / "qdoc", f"""
conf=$(basename "$1" .qdocconf)
echo "$2 $conf" >> {self.calls}
[ -e "$(dirname "$1")/broken" ] && exit 3
mkdir -p "$6"
if [ "$2" = --prepare ]; then grep '^api' "$1" > "$6/$conf.index"; else echo page > "$6/page.xml"; fi
""")
        self.converter_body = f"""
out="${{@: -1}}"
echo "convert $2" >> {self.calls}
mkdir -p "$out/$2" && echo '{{}}' > "$out/$2/module.json" && echo page > "$out/$2/page.md"
echo "rootPage: index" > "$out/toc.yaml"
"""
        self.converter = write_script(self.root / "docbook2markdown6", self.converter_body)
        (self.root / "docs").mkdir()
        self.modules = [self.module("base", ""), self.module("user", "base"), self.module("other", "")]

    def tearDown(self):
        self.tmpdir.cleanup()

    def module(self, name: str, depends: str) -> api_docs.Module:
        source = self.root / "src" / name
        source.mkdir(parents=True)
        (source / f"{name}.qdocconf").write_text(f"depends += \\\n    {depends}\napi = 1\n")
        return api_docs.Module(name, name, source, {}, self.qdoc, source / f"{name}.qdocconf",
                               str(source / "includes.txt"), [str(self.root / "qtdocs")])

    def run_main(self, *extra: str) -> list[str]:
        self.calls.write_text("")
        with unittest.mock.patch.object(api_docs, "discover", return_value=self.modules):
            code = api_docs.main(["--output", str(self.root / "docs" / "api"), "--work", str(self.root / "work"),
                                  "--kde-docs", str(self.root / "kde"), "--converter", self.converter, *extra])
        self.code = code
        return self.calls.read_text().split("\n")[:-1]

    def test_an_unchanged_second_run_does_nothing_and_keeps_the_reference(self):
        first = self.run_main()
        self.assertEqual(len(first), 9)
        self.assertEqual(self.run_main(), [])
        self.assertEqual(sorted(p.parent.name for p in (self.root / "docs" / "api").rglob("page.md")),
                         ["base", "other", "user"])

    def test_a_source_change_redoes_only_that_module(self):
        self.run_main()
        (self.root / "src" / "other" / "notes.txt").write_text("changed\n")
        self.assertEqual(self.run_main(), ["--prepare other", "--generate other", "convert other"])

    def test_dependents_follow_a_changed_index_but_not_an_unchanged_one(self):
        self.run_main()
        (self.root / "src" / "base" / "notes.txt").write_text("not API\n")
        self.assertEqual(self.run_main(), ["--prepare base", "--generate base", "convert base"])
        with (self.root / "src" / "base" / "base.qdocconf").open("a") as conf:
            conf.write("api = 2\n")
        calls = self.run_main()
        self.assertEqual(sorted(calls), sorted(["--prepare base", "--generate base", "--generate user",
                                                "convert base", "convert user"]))

    def test_full_and_a_new_converter_redo_what_they_affect(self):
        self.run_main()
        self.assertEqual(len(self.run_main("--full")), 9)
        write_script(Path(self.converter), self.converter_body + "# version 2\n")
        self.assertEqual(sorted(self.run_main()), ["convert base", "convert other", "convert user"])

    def test_a_failed_module_is_left_out_and_retried(self):
        (self.root / "src" / "other" / "broken").write_text("")
        self.run_main()
        self.assertEqual(self.code, 1)
        self.assertFalse((self.root / "docs" / "api" / "other").exists())
        self.assertEqual(self.run_main(), ["--prepare other"])
        (self.root / "src" / "other" / "broken").unlink()
        self.assertEqual(self.run_main(), ["--prepare other", "--generate other", "convert other"])
        self.assertEqual(self.code, 0)

    def test_depends_are_read_across_continuation_lines(self):
        conf = self.root / "x.qdocconf"
        conf.write_text("include(a)\ndepends += \\\n    kde \\\n    qtcore\ndepends += kconfigcore\n")
        self.assertEqual(api_docs.qdocconf_depends(conf), {"kde", "qtcore", "kconfigcore"})
        conf.write_text("depends = *\n")
        self.assertIsNone(api_docs.qdocconf_depends(conf))


class ReplaceOutputTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.markdown = self.root / "markdown"
        (self.markdown / "kconfigcore").mkdir(parents=True)
        (self.markdown / "toc.yaml").write_text("rootPage: index\n")
        (self.markdown / "kconfigcore" / "kconfig.md").write_text("# KConfig\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_replaces_a_previous_reference_completely(self):
        output = self.root / "api"
        (output / "removedmodule").mkdir(parents=True)
        (output / "toc.yaml").write_text("old\n")
        (output / "removedmodule" / "old.md").write_text("old\n")
        api_docs.replace_output(self.markdown, output)
        self.assertEqual(sorted(str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()),
                         ["kconfigcore/kconfig.md", "toc.yaml"])

    def test_fills_a_missing_or_empty_folder(self):
        for output in (self.root / "missing", self.root / "empty"):
            if output.name == "empty":
                output.mkdir()
            api_docs.replace_output(self.markdown, output)
            self.assertTrue((output / "toc.yaml").is_file())

    def test_refuses_a_folder_that_is_not_a_reference(self):
        output = self.root / "sonic-docs"
        output.mkdir()
        (output / "home.md").write_text("# Home\n")
        with self.assertRaises(ValueError):
            api_docs.replace_output(self.markdown, output)
        self.assertTrue((output / "home.md").is_file())


if __name__ == "__main__":
    unittest.main()
