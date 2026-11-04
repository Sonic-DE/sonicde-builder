#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the generated SonicDE API reference before it is published. Runs offline.

  1. Every internal link (and every toc.yaml page) points to a generated page, and its anchor exists.
  2. Every link into Qt's documentation (https://doc.qt.io/qt-6/) names a page and anchor of the
     installed Qt documentation (its HTML pages and .index files), which the converter linked from.
  3. Every class, struct and union that a module's QDoc index documents has a page and an
     all-members page, and is listed in its module page and in toc.yaml.

Other external links are only counted: sites block automated requests. The summary gives the number
of modules, classes and members, then QDoc's undocumented-symbol warnings and the members the
converter left out, from generate-api-docs' logs. The exit code is 1 when any check fails.

Usage:
    api_docs_check.py [--reference DIR] [--work DIR] [--qt-docs DIR]
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import api_docs

QT_URL = "https://doc.qt.io/qt-6/"
# Classes QDoc doesn't publish.
HIDDEN_STATUS = {"internal", "ignored", "dontdocument"}

FENCE = re.compile(r"^[\s>]*(```|~~~)")
CODE_SPAN = re.compile(r"(`+)(?:.(?!\1))*.?\1")
LINK = re.compile(r"(?<!\\)\[(?:[^\[\]\\]|\\.|\[[^\]]*\])*\]\(<?([^)\s>]*)>?(?:\s+\"[^\"]*\")?\)")
HTML_LINK = re.compile(r"""<a\s[^>]*\bhref=["']([^"']*)["']""", re.IGNORECASE)
ANCHOR = re.compile(r"\{#([^}\s]+)\}\s*$|\b(?:id|name)=[\"']([^\"']+)[\"']", re.MULTILINE)
MEMBER = re.compile(r"^#{3,} .*\{#[^}]+\}\s*$", re.MULTILINE)
TOC_PAGE = re.compile(r"^\s+([^\s:\"]+):\s*\"", re.MULTILINE)

@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    modules: int = 0
    classes: int = 0
    members: int = 0
    internal: int = 0
    qt: int = 0
    external: set[str] = field(default_factory=set)
    site: set[str] = field(default_factory=set)

def links(text: str) -> list[str]:
    """Link targets in Markdown, outside code blocks and code spans."""
    found = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = CODE_SPAN.sub("", line)
        found += LINK.findall(line) + HTML_LINK.findall(line)
    return found

def anchors(text: str) -> set[str]:
    return {first or second for first, second in ANCHOR.findall(text)}

class QtDocs:
    """Pages and anchors of the installed Qt documentation: <module>/<page>.html files and the hrefs
    in the modules' .index files."""

    def __init__(self, root: Path):
        self.pages: dict[str, Path | None] = {}
        self.anchors: dict[str, set[str]] = {}
        for page in root.glob("*/*.html"):
            self.pages.setdefault(page.name, page)
        for index in root.glob("*/*.index"):
            for _, element in ElementTree.iterparse(index):
                href = element.get("href")
                if href and "/" not in href:
                    page, _, anchor = href.partition("#")
                    self.pages.setdefault(page, None)
                    if anchor:
                        self.anchors.setdefault(page, set()).add(anchor)
                element.clear()
        self._read: set[str] = set()

    def has(self, page: str, anchor: str) -> bool:
        if page not in self.pages:
            return False
        if not anchor:
            return True
        if anchor not in self.anchors.get(page, set()) and page not in self._read and self.pages[page]:
            self._read.add(page)
            self.anchors.setdefault(page, set()).update(anchors(self.pages[page].read_text(errors="replace")))
        return anchor in self.anchors.get(page, set())

def check_links(reference: Path, qt: QtDocs, report: Report) -> None:
    root = reference.resolve()
    pages = {path: path.read_text() for path in sorted(root.rglob("*.md"))}
    page_anchors: dict[Path, set[str]] = {}

    def target_anchors(path: Path) -> set[str]:
        if path not in page_anchors:
            page_anchors[path] = anchors(pages[path]) if path in pages else set()
        return page_anchors[path]

    toc = root / "toc.yaml"
    if toc.is_file():
        for name in TOC_PAGE.findall(toc.read_text()):
            if root / f"{name}.md" not in pages:
                report.errors.append(f"toc.yaml: no page {name}.md")
    else:
        report.errors.append("no toc.yaml")

    for path, text in pages.items():
        where = path.relative_to(root)
        for href in links(text):
            if href.startswith(QT_URL):
                report.qt += 1
                page, _, anchor = href[len(QT_URL):].partition("#")
                if not qt.has(page, unquote(anchor)):
                    report.errors.append(f"{where}: not in the Qt documentation: {href}")
                continue
            if re.match(r"[a-z][a-z0-9+.-]*:", href, re.IGNORECASE):
                if href.lower().startswith(("http:", "https:")):
                    report.external.add(href.partition("#")[0])
                continue
            if href.startswith("/"):
                report.site.add(href)  # the site's own pages, e.g. /docs
                continue
            report.internal += 1
            file, _, anchor = unquote(href).partition("#")
            target = (path.parent / file).resolve() if file else path.resolve()
            if not target.is_relative_to(root) or not target.exists():
                report.errors.append(f"{where}: no such file: {href}")
            elif anchor and target.suffix == ".md":
                if anchor not in target_anchors(target):
                    report.errors.append(f"{where}: no anchor #{anchor} in {file or where.name}: {href}")

def check_classes(reference: Path, index_root: Path, report: Report) -> None:
    toc = (reference / "toc.yaml").read_text() if (reference / "toc.yaml").is_file() else ""
    toc_pages = set(TOC_PAGE.findall(toc))
    for folder in sorted(path for path in index_root.iterdir() if path.is_dir()):
        module = folder.name
        if not (reference / module).is_dir():
            report.errors.append(f"{module}: has an index but no pages")
            continue
        report.modules += 1
        listed = "\n".join(path.read_text() for path in (reference / module).glob("*-module.md"))
        for index in sorted(folder.glob("*.index")):
            for _, element in ElementTree.iterparse(index):
                if (element.tag not in ("class", "struct", "union") or element.get("status") in HIDDEN_STATUS
                        or element.get("documented") != "true"):
                    continue
                href = element.get("href", "")
                name = element.get("fullname") or element.get("name", "")
                if not href.endswith(".html") or "#" in href or "/" in href:
                    continue
                stem = href.removesuffix(".html")
                report.classes += 1
                page = reference / module / f"{stem}.md"
                if not page.is_file():
                    report.errors.append(f"{module}: {name} has no page {module}/{stem}.md")
                    continue
                report.members += len(MEMBER.findall(page.read_text()))
                if not (reference / module / f"{stem}-members.md").is_file():
                    report.errors.append(f"{module}: {name} has no all-members page {module}/{stem}-members.md")
                # As in Qt's reference, deprecated classes aren't in the module's class list.
                if element.get("status") != "deprecated" and f"({stem}.md)" not in listed:
                    report.errors.append(f"{module}: {name} isn't listed in its module page")
                if f"{module}/{stem}" not in toc_pages:
                    report.errors.append(f"{module}: {name} isn't in toc.yaml")

def skipped(logs: Path) -> list[str]:
    """QDoc's undocumented-symbol warnings and the members the converter left out."""
    lines = []
    for log in sorted(logs.glob("generate-*.log")):
        lines += [line.strip() for line in log.read_text(errors="replace").splitlines() if "warning: Undocumented" in line]
    for log in sorted(logs.glob("convert-*.log")):
        lines += [line.strip() for line in log.read_text(errors="replace").splitlines()
                  if "(WW)" in line or "left out" in line]
    return lines

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the generated SonicDE API reference before publishing it.")
    parser.add_argument("--reference", type=Path, default=api_docs.DOCS_CHECKOUT / "api",
                        help="The reference to check (default: api/ in the builder's sonic-docs checkout).")
    parser.add_argument("--work", type=Path, default=api_docs.ROOT / "state" / "api-docs",
                        help="generate-api-docs' work directory, for its indexes and logs (default state/api-docs).")
    parser.add_argument("--qt-docs", type=Path,
                        help="Qt's documentation directory (default: the one ECM found for the builds).")
    parser.add_argument("--build-root", type=Path, default=api_docs.ROOT / "build" / "sonicde")
    args = parser.parse_args(argv)

    qt_docs = args.qt_docs or api_docs.qt_docs_dir(api_docs.discover(args.build_root))
    if qt_docs is None or not qt_docs.is_dir():
        print("No Qt documentation directory; pass --qt-docs.", file=sys.stderr)
        return 1
    if not (args.work / "index").is_dir():
        print(f"No indexes in {args.work / 'index'}; run ./generate-api-docs first.", file=sys.stderr)
        return 1

    report = Report()
    check_links(args.reference, QtDocs(qt_docs), report)
    check_classes(args.reference, args.work / "index", report)

    print(f"{report.modules} modules, {report.classes} classes, {report.members} documented members")
    print(f"{report.internal} internal links, {report.qt} Qt links, {len(report.site)} site pages "
          f"({', '.join(sorted(report.site))}), {len(report.external)} other external pages (not fetched)")
    notes = skipped(args.work / "logs")
    if notes:
        print(f"{len(notes)} undocumented or left-out symbols:")
        for line in notes:
            print(f"  {line}")
    if report.errors:
        print(f"{len(report.errors)} problems:")
        for error in report.errors:
            print(f"  {error}")
        return 1
    print("No problems")
    return 0

if __name__ == "__main__":
    sys.exit(main())
