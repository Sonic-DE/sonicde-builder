SonicDE standalone builder
==========================

This is a standalone builder for [SonicDE](https://github.com/Sonic-DE/)
(fork of KDE with full X11 compatibility) with a Python + CMake
superbuild pipeline.

howto:
------

* install dependencies: `pip install pyyaml` and a working Qt6 + CMake 3.28+
* fetch one Git repository: `./fetch sonic-browser-integration`
* fetch all active Git repositories: `./fetch-all`
* full build: `./build-all`
* install the last build without rebuilding: `./install`
* create dependency graph: `./generate-depgraph`
* generate the API reference for sonicde.org from every built library (needs a build including
  sonic-frameworks-doctools, and Qt's documentation with its `.index` files installed where
  `qtpaths6 --query QT_INSTALL_DOCS` says) into `src/sonicde/sonic-docs/api`:
  `./fetch sonic-docs`, then `./generate-api-docs`; commit and push sonic-docs to publish it.
  Later runs only redo libraries whose source changed (and those linking to their changed API);
  `--full` redoes everything
* sync one repository (autopick): `./sync sonic-browser-integration`
* sync all managed repositories: `./sync-all`
* reset repos after merged PRs: `./sync-success-cleanup`
* cleanup failed autopick runs: `./sync-fail-cleanup --force`
* reset trackers (recovery): `./reset-trackers --yes`
* check for missing GitHub repos: `./check-new-repos`
* list OS packages to install: `./install-deps`
* preview stale Frameworks debug exports: `./cleanup-stale-cmake-debug`
* merge per-project graphify graphs: `./merge-graphs`
* bump Core CMake version: `./version-bump-core 6.8.0`
* tag accepted Core releases: `./version-bump-tag-core 6.8.0`
* bump Core dependency requirements: `./version-bump-project-dep 6.8.0`
* bump Frameworks CMake version: `./version-bump-frameworks 6.31.0`
* tag accepted Frameworks releases: `./version-bump-tag-frameworks 6.31.0`
* bump System CMake version: `./version-bump-system 26.08.1`
* tag accepted System releases: `./version-bump-tag-system 26.08.1`
* list repos in a release category: `./version-bump-core --list`

`sync-all` invokes `sync REPOSITORY` for each managed checkout and records
failures in `state/failed-sync-repos.txt`.

`fetch REPOSITORY` fetches exactly one configured repository, without its
dependencies. It accepts an unambiguous basename or a qualified package name
such as `sonicde/sonic-browser-integration` or `3rdparty/qcoro6`. It loads the
current manifests, including configured repositories outside the active build.
`fetch-all` generates the active model once and invokes `fetch` for each selected
repository, continues after failures, and returns failure if any fetch failed.
Existing checkouts keep their HEAD, branch, index, and worktree as before.
Explicit tag refspecs are respected without adding a second `--tags` mapping.
For example, `refs/tags/*:refs/tags/origin/*` fetches tags into the `origin/`
namespace only, avoiding collisions if the remote itself has namespaced tags.
Remotes without explicit tag mappings still fetch tags using `--tags`.

    ./fetch sonic-browser-integration --dry-run
    ./fetch-all --dry-run
    ./fetch-all --only sonicde/sonic-screen

stale debug CMake exports:
--------------------------

Old debug installations can leave `KF6*Targets-debug.cmake` fragments referring
to libraries that no longer exist. Preview them on the affected build host:

    ./cleanup-stale-cmake-debug

Remove confirmed stale fragments explicitly (elevate only when required):

    sudo ./cleanup-stale-cmake-debug --apply

Use `--prefix /opt/sonicde` or `--prefix state/install-stage/usr` to inspect
another installation; `--prefix` can be repeated. The default prefix is `/usr`.
The command does not hard-code a Frameworks version, execute CMake code, or
remove main config/target files. It removes a debug fragment when any file it
references is missing, without requiring a release or other replacement export.
Healthy debug exports are retained. Unsupported or unsafe fragments are
reported and retained. Run it between builds and
installations. It is not invoked automatically by `build-all`.

version bumps:
--------------

CMake bumps and release tagging are separate operations. Both use the same
three explicitly maintained repository lists:

* Core: `scripts/version-repos-plasma.list` (the existing release list is retained)
* Frameworks: `scripts/version-repos-frameworks.list`
* System: `scripts/version-repos-system.list`

The lists were populated from the latest SonicDE release tags in each
repository (`6.7.5[.patch]`, `6.30.0[.patch]`, and `26.08.0[.patch]`,
respectively). Repositories with different version schemes are excluded.
The scripts read these lists as-is; they do not discover repositories by
searching CMake files or tags at release time. Maintain the lists explicitly
when a repository joins or leaves a release group.

For each listed repository, the regular `version-bump-core`,
`version-bump-frameworks`, and `version-bump-system` commands:

1. Fetch `origin` and check out local `master` at `origin/master`.
2. Update existing release-version fields in `CMakeLists.txt`, if present.
3. Commit the CMake change only if the file actually changed.
4. Push only the updated `master` to `origin/master` unless `--no-push` is specified.

These commands never create or push tags. Existing tags do not prevent CMake
updates. Repositories without matching version fields, or already at the
requested version, need no commit and are left unchanged. Unlike KDE's release
scripts, no second "bump to next dev version" commit is created.

    ./version-bump-core 6.8.0
    ./version-bump-frameworks 6.31.0
    ./version-bump-system 26.08.1

The version argument must be in `X.Y.Z` format. Use `--dry-run` to preview
CMake changes without modifying files, commits, tags, or remotes:

    ./version-bump-core 6.8.0 --dry-run

Use `--list` to print the maintained list without modifying anything:

    ./version-bump-core --list
    ./version-bump-frameworks --list
    ./version-bump-system --list

After the version commits have been accepted on `origin/master`, tag separately:

    ./version-bump-tag-core 6.8.0 --dry-run
    ./version-bump-tag-core 6.8.0
    ./version-bump-tag-frameworks 6.31.0
    ./version-bump-tag-system 26.08.1

The `version-bump-tag-*` commands fetch and prepare `origin/master` using the
same branch protections as the regular commands. They never edit CMake files
or create commits. They verify that any existing release-version fields at the
accepted tip match the requested version, then tag that exact commit and push
only that tag. Listed repositories without release-version fields can still be
tagged. An existing local tag is reused only if it already points to that same
commit; conflicting local or remote tags are errors and are never force-moved.
`--no-push`, `--dry-run`, and `--list` are also supported. Tag previews inspect
the locally fetched `origin/master` ref without fetching or switching branches.

The usual CMake edit patterns are `set(PROJECT_VERSION "X.Y.Z")` for
**Core**, `set(KF_VERSION "X.Y.Z")` for **Frameworks**, and the three
`set(RELEASE_SERVICE_VERSION_MAJOR/MINOR/MICRO "X")` variables for
**System**. Frameworks also updates `set(KF_DEP_VERSION "X.Y.Z")` and
literal `find_package(ECM X.Y.Z)` requirements when present. Core
supports a literal `project(... VERSION X.Y.Z)` declaration, and
`sonic-frameworks-cmake-modules` updates its existing `set(VERSION ...)`.
These are editing rules, not membership tests. Missing fields are not
invented; unrelated dependency and library ABI versions are left alone.

Each script requires a clean working tree in every repo it touches.
Repos with uncommitted tracked changes are skipped with a warning. Listed
repositories whose checkouts are missing are also reported and skipped;
they remain in the maintained release list.
Existing local `master` branches are only fast-forwarded; unpushed/diverged
commits are never reset away. A missing local `master` is created tracking
`origin/master`. Active rebases/merges and detached commits outside
`origin/master` are refused. `--dry-run` and `--list` remain read-only: they
inspect the current checkouts without fetching or switching branches.

The separate dependency command scans all checked-out SonicDE repositories,
not just the Core release list. It updates existing literal
`set(PROJECT_DEP_VERSION ...)` declarations in tracked CMake files, including
nested files. It does not change `PROJECT_VERSION`, create missing declarations,
edit build-generated files, or create tags. Each changed repository receives a
commit named `Project Dependency VERSION`, for example `Project Dependency 6.8.0`.
It prepares `master` at `origin/master` before discovering declarations, so
the bump never writes stale contents from a previous branch. Commits are
pushed to `origin/master` unless `--no-push` is given. The same clean-tree
and local-commit protections described above apply.

    ./version-bump-project-dep 6.8.0 --dry-run
    ./version-bump-project-dep 6.8.0 --no-push
    ./version-bump-project-dep 6.8.0

The dependency command also supports `--list` to list repositories containing
the variable, including repositories already at the requested version.

build and system installation:
------------------------------

Build everything for `/usr`, then install it to the system:

    ./build-all --parallel 8
    ./install

The meta-build schedules one package at a time so nested Ninja processes cannot
multiply the requested concurrency. `--parallel N` controls compilation inside
that package. Without it, the builder chooses a conservative value from available
memory (roughly one job per 2 GiB after reserving 1 GiB, capped at eight). Set
`SONICDE_BUILD_JOBS=N` for a persistent override.

Or build and install in one command:

    ./build-all --install --parallel 8

Select a different final destination with:

    ./build-all --prefix /opt/sonicde --parallel 8

Packages are always configured for the final destination, while every
installation during the build is redirected with `DESTDIR` under
`state/install-stage/`. Later packages discover these staged dependencies, and
programs built against them find them in `state/install-stage/usr` (or the
chosen prefix under `state/install-stage/`).
**No live-system installation starts until the entire meta-build succeeds.**
If validation, configuration or any build fails, deployment is not run.
Partial-target builds are not accepted by this wrapper.

After the full build succeeds, the wrapper runs `sudo -v` to authenticate
(prompting for a password when required by sudo policy), then runs only the
installation helper under sudo. Cancelling or failing authentication prevents
deployment. Configuration and compilation are not elevated. `./install` is
that deployment-only step, for any completed build:

    ./install

It installs the packages of `state/model.json` to the prefix they were built
for, without configuring or building. It checks all package installation
scripts and configured prefixes before sudo authenticates, refuses a build
configured for the builder's own `build/` directory (made before builds
targeted the system prefix), skips system/no-build packages, and stops on the
first deployment error.
Deployment is not transactional: an install failure can leave earlier packages
installed, although no packages are deployed while the meta-build is incomplete.

Build one repository instead of the whole solution:

    ./build-repo sonic-firewall --parallel 8
    ./build-repo sonic-firewall --install --parallel 8

`build-repo` takes a repository name (or qualified package name, as `./fetch`
does) and builds that repository, every package that depends on it directly or
transitively (so they are rebuilt against it), and the prerequisites of all of
those. A leaf such as `sonic-firewall` builds only itself and its
prerequisites; a low-level framework rebuilds much of the desktop. It accepts
the same options as `build-all`, and `--install` deploys exactly the selected
packages with the same full-build barrier. The selection is written to
`state/build-model.json` and `state/build-depgraph.dot`, leaving the full
solution's `state/model.json` untouched. To install a completed `build-repo`
build:

    ./install --model state/build-model.json

Both commands regenerate the shared superbuild in `build/` for their selection,
so run `build-all` again before relying on a full-solution build tree.

architecture:
-------------

The builder consists of Python scripts that parse the SonicDE solution
and package YAML, resolve the dependency closure, and generate a CMake
superbuild using `ExternalProject_Add`.

    scripts/config.py        parse YAML, resolve closure, emit model.json
    scripts/repositories.py  generate repository views and repo-list
    scripts/fetch.py         fetch Git repositories from manifest metadata
    scripts/validate.py      validate system dependencies and Qt6 Core
    scripts/generate.py      generate CMake ExternalProject superbuild
    scripts/install.py       deploy completed CMake packages after the full build
    scripts/git-autopick     upstream sync (rebase tracker range onto master)

configuration:
--------------

The `config/sonicde/` tree is imported from the SonicDE meta-build
workspace. The solution file (`config/sonicde/solutions/sonicde.yaml`) defines the
build list, install prefix, environment, and fetch refspecs. Package
manifests under `config/sonicde/packages/` define Git sources, dependencies, and
build systems.

Only CMake and no-build packages are supported. System packages are
validated via `pkg-config`. Qt6 is supplied by the operating system
and is deliberately not fetched or built.

tests:
------

Run the test suite with:

    python3 -m pytest tests/ -v

All tests use local Git repositories and fixture CMake projects. No
remote repositories are contacted.
