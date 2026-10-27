# SonicDE Standalone Builder

This is a standalone build orchestration project for
[SonicDE](https://github.com/Sonic-DE/) (fork of KDE with full X11
compatibility). It replaces the former MPBT workspace with a Python +
CMake superbuild pipeline.

## Directory Structure

```text
sonicde-builder-wip/
├── AGENTS.md
├── README.md
├── CMakeLists.txt                     # top-level superbuild (Qt6 probe + include)
├── sync                              # run upstream sync (autopick) for one repo argument
├── sync-all                          # run upstream sync (autopick) for all repos
├── sync-fail-cleanup                 # destructive recovery (--force required)
├── sync-success-cleanup              # reset repos to origin/master after PRs merge
├── sync-tags                         # mirror upstream release tags onto SonicDE commits
├── build-all                          # full build: config -> validate -> generate -> cmake
├── build-repo                         # one repository + its dependents + their prerequisites
├── check-new-repos                    # compare GitHub org listing against managed repos
├── release-notes-changes              # list SonicDE changes since each repo's last release
├── fetch                              # fetch one repository argument
├── fetch-all                          # fetch all active repositories through fetch
├── generate-depgraph                 # generate DOT dependency graph
├── install-deps                       # list OS packages needed by active system deps
├── merge-graphs                       # merge per-project graphify graphs into root graph
├── reset-trackers                     # reset autopick trackers (recovery, --yes required)
├── repo-list                          # generated compatibility artifact
├── config/
│   ├── repos.list                     # maintained SonicDE repository inventory
│   └── sonicde/
│       ├── solutions/sonicde.yaml     # active solution (build list, prefix, env, fetch)
│       └── packages/                  # package manifests (sonicde, 3rdparty, os-installed, qt6)
├── cmake/
│   └── SonicDEProjects.cmake         # generated ExternalProject definitions
├── scripts/
│   ├── config.py                      # parse YAML, resolve closure, emit model.json
│   ├── repositories.py                # generate repository views and repo-list
│   ├── fetch.py                       # fetch Git repositories from manifest metadata
│   ├── validate.py                    # validate system deps (pkg-config) and Qt6 Core
│   ├── generate.py                    # generate CMake ExternalProject superbuild + DOT
│   ├── sync_tags.py                   # mirror upstream tags onto SonicDE commits
│   └── git-autopick                   # upstream sync (rebase tracker range onto master)
├── src/
│   ├── 3rdparty/<project>/            # one independent Git repository per project
│   └── sonicde/<project>/             # one independent Git repository per project
├── build/                             # CMake build directory (generated)
├── state/
│   ├── model.json                     # normalized active closure model (generated)
│   ├── logs/                          # per-command and test logs
│   ├── failed-sync-repos.txt          # upstream sync failure list
│   ├── failed-sync-tag-repos.txt      # tag sync failure list
│   └── success-sync-tag-repos.txt     # successful tag sync list
└── tests/                             # Python unit tests (pytest/CTest)
    ├── fixtures/                      # shared local Git repo / CMake fixtures
    ├── test_config.py
    ├── test_config_comprehensive.py
    ├── test_fetch.py
    ├── test_fetch_comprehensive.py
    ├── test_superbuild.py
    ├── test_autopick.py
    ├── test_autopick_success_cleanup.py
    ├── test_sync_tags.py
    ├── test_panelview.py
    └── test_sonic_win_rename.py
```

## Context Protocol

### Worktree Policy

Never create Agent Manager worktrees or invoke `agent_manager` with
`mode: "worktree"`. Work only in the current checkout. This restriction
persists across sessions and may only be changed by an explicit user edit to
`kilo.json`.

1. This project is its own Git repository. Commit builder changes here;
   do not commit generated artifacts (`src/*/`, `build/`, `state/`,
   `cmake/`, `repo-list`, `depgraph.*`, `graphify-out/`).
2. Every immediate child of `src/3rdparty/` and `src/sonicde/` is a
   separate independent Git repository fetched by `fetch` or `fetch-all`. Run Git
   commands inside the specific project repository being changed, not
   from the project root.
3. The `config/sonicde/` tree is the imported build configuration. It is
   authoritative for package metadata, dependency resolution, and
   repository definitions. Do not edit it to work around a builder bug;
   fix the builder.
4. Do not modify `sonicde-meta-build` or its `_WORK_/` workspace. This
   project is the replacement, not a companion.
5. When a change crosses package boundaries, inspect every affected
   repository and verify dependency, API, and build-system alignment
   using `generate-depgraph` and `state/model.json`.

## Builder-Owned Dependency Chain

The builder builds the **entire SonicDE dependency chain, including all
Frameworks packages**. Do not treat SonicDE Frameworks as system-provided KDE
dependencies or assume a distribution's installed KF6 packages are acceptable
substitutes for the builder's own packages.

Every required SonicDE package must be represented in the consuming package's
manifest, so its build and staged installation finish before the consumer is
configured. Verify requirements against SonicDE's actual CMake sources and map
the CMake package names to the corresponding SonicDE repository names.

If CMake selects a system KF6 config instead of the staged SonicDE provider,
investigate missing dependency edges, build/install ordering, staged discovery,
and source/version alignment. Fix the verified cause in the builder or its
manifests; do not use installing distribution Frameworks packages as a substitute
for correcting the builder-owned chain.

Qt6 and dependencies explicitly declared under `os-installed/` remain supplied
by the operating system. They are distinct from builder-owned SonicDE packages.

## Logging in SonicDE Code

SonicDE code logs only through **Sonic Logging** (`sonic-frameworks-logging`,
`Sonic::Logging`). Use it wherever possible, in new code and whenever you touch
code that logs another way.

- Declare the binary's component once, in exactly one `.cpp` of each library,
  plugin or executable: `SONIC_LOGGING_COMPONENT("org.sonicde.<component>")`.
- Log with `sonicDebug()`, `sonicInfo()`, `sonicWarning()`, `sonicCritical()`
  and `sonicFatal()`. `sonicFatal()` logs a critical line and aborts.
- Executables call `Sonic::Logging::install()` early in `main()`. A library
  loaded into another program's process uses `SONIC_LOGGING_AUTO_INSTALL()`
  instead.
- Test binaries that compile logging sources declare their own component and
  don't call `install()`.
- Add `sonicde/sonic-frameworks-logging` to the package manifest's `depends`
  and link `Sonic::Logging`.

Replace the older methods when you find them:

- `qCDebug`, `qCInfo`, `qCWarning`, `qCCritical` and `qCFatal`;
- `Q_LOGGING_CATEGORY`, `Q_DECLARE_LOGGING_CATEGORY`,
  `ecm_qt_declare_logging_category` and `ecm_qt_install_logging_categories`;
- custom `qInstallMessageHandler` handlers and per-program log files.

Remove the generated category headers and `.categories` files along with them,
and the `#include <QDebug>`, `<QtDebug>` and `<QLoggingCategory>` lines that
only served the old calls. `<Sonic/Logging/Log>` already brings in what the
macros need. Keep such an include only when the file uses the type itself, for
example to declare a `QDebug operator<<`.

Plain `qDebug()`, `qInfo()`, `qWarning()` and `qCritical()` are only for code
that can't depend on Sonic Logging. Keep a Qt logging category only when there
is a concrete reason, such as an upstream API that requires one, and say why in
a comment.

## Communication Between SonicDE Components

SonicDE components talk to each other through **Sonic IPC**
(`sonic-frameworks-ipc`, `Sonic::Ipc`): framed messages over Unix domain
sockets, with no message bus. During regular development, whenever you come
across **internal-only** D-Bus communication, replace it with Sonic IPC.

Internal-only means both ends ship in SonicDE and nothing outside SonicDE
calls the interface. Examples already converted: the keyboard layout applet and
the keyboard service; the login manager's greeter, settings page and helper;
the screen locker and its unlock screen.

If software outside our repositories takes part, as client or as server, the
D-Bus side stays. If only SonicDE components use it, it goes.

- **A D-Bus client is easy to judge:** it's external when the service it calls
  isn't provided by one of our repositories (logind, NetworkManager, UPower,
  polkit, a third-party application, ...).
- **A D-Bus server we export needs evidence**, because any program can call it.
  Treat it as external if any of these is true:
  1. It's a freedesktop.org or other published interface, such as
     notifications, the screen saver, status notifier items, MPRIS or portals.
  2. Its interface XML is installed into the D-Bus interfaces directory, which
     offers it as public API.
  3. A source search finds callers outside our repositories. Search
     <https://codesearch.debian.net> for the service name, object path and
     interface name. A hit in software we don't ship counts as external; a hit
     in the upstream package one of our repositories forked doesn't. Search
     our own repositories too, so you know every internal caller.
  4. It's an `org.kde.*`, `kde.*` or `kf6.*` interface or service
     (including installed interface files with those prefixes, such as
     `kf6_org.freedesktop.ScreenSaver.xml`) that upstream KDE applications call. Users install those applications alongside SonicDE,
     so they are external consumers.

  Watching the bus on the test VM (`busctl --user monitor <service>`, then
  `busctl status <sender>`) shows who calls it there. Use it as supporting
  evidence only, because it only sees what's installed.
- **When you're unsure, keep the D-Bus server** and move our own clients to
  Sonic IPC. Remove the server later, once nothing external has turned up, and
  say in the change what you checked.

When you convert an interface:

- Change both ends in the same change, and remove the D-Bus XML, adaptors and
  interface classes that only served it.
- Put the message types and a protocol version in one shared header, and send
  each message as one frame (see `IpcProtocol.h` in sonic-desktop-interface's
  keyboard layout applet).
- Listen on `Server::userSocketPath("<name>.sock")` with `Access::User`. A
  privileged server checks the peer credentials of each connection.
- Raise the protocol version on any incompatible change. Note in the packager
  release notes if a restart is needed after upgrading, and in the package's
  `RELEASE-NOTES.md` if users will notice.
- Add the `sonicde/sonic-frameworks-ipc` dependency to the manifest, link
  `Sonic::Ipc`, and test both ends.

## Release Notes

Every SonicDE repository keeps a `RELEASE-NOTES.md` at its root that records
what SonicDE changed in that package. **When you finish a task that changes a
repository, add the change to that repository's `RELEASE-NOTES.md` before
reporting the task complete.**

- New entries go under `## Unreleased` at the top of the file; create that
  heading if it isn't there. When a release is tagged, the `Unreleased`
  heading becomes the release's version.
- List only SonicDE's own changes, the ones that differ from upstream. Leave
  out anything that came from an upstream sync. Tags that start with `v`, such
  as `v6.8.0`, are upstream releases, not ours.
- Only note fixes to code that has shipped. A fix to something still waiting
  for release (a new feature, or a whole new package, that's only under
  `## Unreleased`) is part of that pending change, not a new item: leave it
  out, or reword the existing bullet if the fix changes what it says. Check
  with `git tag --contains <commit>` on the commit that introduced the broken
  code; no SonicDE release tag means it hasn't shipped.
- A release with no SonicDE changes keeps its heading, with the line "No
  SonicDE-specific changes in this release; it brings the package up to date
  with upstream."
- Write for users, not developers. Use plain, positive, readable language that
  says what is better for the person using SonicDE, not how the code changed.
  Avoid class names, file paths, commit hashes and jargon.
- Give each change its own bullet. Changes users can't see (build tooling,
  code cleanups, version bumps, tests) get one short "Behind the scenes" bullet
  per release instead of one each.

Release order for a group (`core`, `frameworks` or `system`):

1. `./version-bump-<group> <version>` commits and pushes the version bump.
2. `/release-notes <group> <version>` (the `release-notes` skill) writes the
   release's sections from `./release-notes-changes <group>`, which lists
   SonicDE's own changes since each repository's last release.
3. After review, `./version-bump-tag-<group> <version>` commits and pushes each
   repository's `RELEASE-NOTES.md` to master, then tags that commit. It refuses
   to tag a repository whose notes have no section for the version.

The builder root's `RELEASE-NOTES.md` is different: it's for packagers and is
organised by release, newest first. When a task adds, renames or removes a
package, changes a dependency or needs packagers to act, record it under
`## Upcoming release` in the matching subsection: "Packages added", "Packages
renamed", "Packages removed", "Dependency changes" or "Actions". Keep the
package index at the end current as well. When a release is tagged, rename
"Upcoming release" to that release and add it to the table at the top.

## Adding or Renaming SonicDE Packages

Release version edits and tagging are separate: `version-bump-core`,
`version-bump-frameworks`, and `version-bump-system` only update/commit CMake
versions and push `master`. `version-bump-tag-core`,
`version-bump-tag-frameworks`, and `version-bump-tag-system` only tag the accepted
`origin/master` commit and push that specific tag. Never push all branches or
all tags. `version-bump-project-dep` remains dependency-only and never tags.

### SonicDE and Upstream Release Tags

SonicDE release tags are **unprefixed**, for example `6.8.0`. Tags beginning
with **`v`**, such as `v6.8.0`, are KDE upstream tags, not SonicDE releases.
When auditing SonicDE package versions or selecting the latest SonicDE release,
compare against unprefixed release tags; do not strip `v` and treat upstream
tags as equivalent SonicDE releases. The two tags may legitimately point to
different commits; that alone is not a tag inconsistency. Do not move or delete
either tag merely because their version numbers match.

### Required Package Lists

When adding a SonicDE repository/package, update **all applicable maintained
lists in the same change**, not just its package manifest:

1. **`config/repos.list`** — add the repository basename, e.g.
   `sonic-power-management`.
2. **`config/sonicde/solutions/sonicde.yaml`** — add the package to `build`,
   using its full name, e.g. `sonicde/sonic-power-management`.
3. **The matching release list**, when the package belongs to a release group:
   - Core: `scripts/version-repos-plasma.list` (the legacy filename is retained).
   - Frameworks: `scripts/version-repos-frameworks.list`.
   - System: `scripts/version-repos-system.list`.

Also add its manifest under `config/sonicde/packages/sonicde/`. Use the current
canonical repository name consistently. For renames, replace the old name in
every affected list and rename/update the manifest; do not leave stale entries
or add duplicates.

The root **`repo-list` is generated**, not a substitute for either maintained
list under `config/`. Regenerating it alone does not register a new package.
Before reporting the work complete, explicitly verify membership in both
`config/repos.list` and `config/sonicde/solutions/sonicde.yaml`, plus the
applicable release list.

## Build Pipeline

The builder operates in distinct phases, each implemented as a Python
module invoked by the root command scripts:

1. **Config** (`scripts/config.py`): Parse the solution and package YAML,
   interpolate `${...}` variables, resolve the active dependency closure
   (exact names then unique `provides`), detect cycles, and write
   `state/model.json` atomically.
2. **Validate** (`scripts/validate.py`): Run `pkg-config --modversion`
   for every active system package and configure a real C++ CMake probe
   for `find_package(Qt6 REQUIRED COMPONENTS Core)`.
3. **Fetch** (`scripts/fetch.py`): Clone or update Git repositories from
   manifest metadata. Existing checkouts are updated without mutating
   HEAD, branch, index, or untracked files.
4. **Generate** (`scripts/generate.py`): Emit `cmake/SonicDEProjects.cmake`
   with `ExternalProject_Add` targets and `DEPENDS` edges from the
   resolved closure.
5. **Build** (`build-all`): Configure the parent CMake project and build
   the requested targets.

## Autopick

`scripts/git-autopick` synchronizes forked repositories with their
upstream sources. It is run for one named repository by `sync` and for all
managed repositories by `sync-all`.

Key invariants:

- The tracker records an upstream commit that has been processed. It
  always remains on upstream history and is never replaced by a rebased
  commit identity.
- The temporary sync branch starts from upstream and rebases the
  tracker-to-upstream work onto master.
- If the rebased branch has the same commit as master, no PR is created;
  the temporary branch is removed and the tracker advances.
- Existing remote sync branches are checked before starting new work so
  an open or unmerged sync branch is not duplicated.

`sync-tags` (via `scripts/sync_tags.py`) is a separate flow: it mirrors
each upstream's latest release tag onto the patch-equivalent SonicDE
commit. It does not create PRs. Results land in
`state/{failed,success}-sync-tag-repos.txt`.

## Testing

Run the full test suite:

```bash
python3 -m pytest tests/ -v
```

All tests use local Git repositories, fixture CMake projects, fake
`pkg-config`, and fake `gh`. No remote repositories are contacted.

When adding new features or fixing bugs, add a test that covers the
specific scenario. The test baseline (captured `pytest -v` output) can
be regenerated into `state/logs/test-baseline.log`; it is a local-only
artifact, gitignored along with the rest of `state/`.

## Graphify

When the user invokes `/graphify`, load and follow the installed
Graphify skill before doing anything else.

Rules:

- For codebase questions about checked-out source repositories under
  `src/`, run `graphify query "<question>"` from the specific project
  directory under `src/<group>/<project>/`.
- The builder's own `scripts/` and `tests/` are small enough to explore
  directly; Graphify is not required for builder-internal questions.
- After changing source code in a checked-out repository, run
  `graphify update .` inside that project directory.
- Do not run Graphify from the project root expecting nested
  repositories under `src/` to be discovered automatically. `detect()`
  prunes `graphify-out/` at any depth and honors `.gitignore` (which
  ignores `src/*/`), so a bare `/graphify .` at the root graphs the
  builder only — never the SonicDE sub-projects.
- The root `./merge-graphs` wrapper composes the per-project graphs into
  the merged cross-repo graph at `graphify-out/graph.json`. It globs
  `src/sonicde/*/graphify-out/graph.json` and
  `src/3rdparty/*/graphify-out/graph.json`, requires at least two graphs,
  and prefixes node IDs per repo so same-named symbols across sub-projects
  do not collide. This is a full rebuild, not incremental.
  The user runs this command; agents must not run it automatically.
- For cross-repo or whole-SonicDE questions, run `graphify query
  "<question>"` at the project root against the merged
  `graphify-out/graph.json`. If it is missing or stale, report that to the
  user rather than running `merge-graphs`.

## Conventions

- Only CMake and no-build source packages are supported. Meson and other
  build systems are rejected during closure validation.
- Qt6 is supplied by the operating system and is deliberately not fetched
  or built. Active `qt6/*` source dependencies are rejected.
- Binary-package images and tarballs are not implemented.
- The `repo-list` file is a generated compatibility artifact, not
  authoritative. Repository views derive from the normalized model.
- Root command names:
  `sync`, `sync-all`, `sync-fail-cleanup`, `sync-success-cleanup`, `sync-tags`,
   `build-all`, `build-repo`, `check-new-repos`, `fetch`, `fetch-all`, `generate-depgraph`,
  `install-deps`, `merge-graphs`, `reset-trackers`.
