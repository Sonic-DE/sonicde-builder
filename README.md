SonicDE standalone builder
==========================

This is a standalone builder for [SonicDE](https://github.com/Sonic-DE/)
(fork of KDE with full X11 compatibility) with a Python + CMake
superbuild pipeline.

Warning: it's still an early work-in-progress.

howto:
------

* install dependencies: `pip install pyyaml` and a working Qt6 + CMake 3.28+
* just fetch git repos: `./fetch-all`
* full build: `./build-all`
* create dependency graph: `./generate-depgraph`
* run upstream sync (autopick): `./sync`
* reset repos after merged PRs: `./sync-success-cleanup`
* cleanup failed autopick runs: `./sync-fail-cleanup --force`
* reset trackers (recovery): `./reset-trackers --yes`
* check for missing GitHub repos: `./check-new-repos`
* list OS packages to install: `./install-deps`
* merge per-project graphify graphs: `./merge-graphs`
* bump Plasma version and tag: `./version-bump-plasma 6.7.5`
* bump Frameworks version and tag: `./version-bump-frameworks 6.30.0`
* bump System version and tag: `./version-bump-system 26.04.3`
* list repos in a category: `./version-bump-plasma --list`

version bumps:
--------------

Three scripts bump the version across all repos in a release group.
Each script identifies its repos by the version-variable pattern in
`CMakeLists.txt` (not tags), updates the version, commits, and creates
a `v<version>` tag.  Unlike KDE's release scripts, no second "bump to
next dev version" commit is created -- we make patch releases against
these tagged versions (e.g. `6.7.4.1`).

    ./version-bump-plasma 6.7.5
    ./version-bump-frameworks 6.30.0
    ./version-bump-system 26.04.3

The version must be in `X.Y.Z` format.  Repos already at the target
version are skipped.  Use `--dry-run` to preview:

    ./version-bump-plasma 6.7.5 --dry-run

Use `--list` to print the matching repos without modifying anything:

    ./version-bump-plasma --list
    ./version-bump-frameworks --list
    ./version-bump-system --list

**Plasma** repos use `set(PROJECT_VERSION "X.Y.Z")` in CMakeLists.txt.
**Frameworks** repos use `set(KF_VERSION "X.Y.Z")` and additionally
update `set(KF_DEP_VERSION "X.Y.Z")` and `find_package(ECM X.Y.Z)`
when present.  **System** repos use the three
`set(RELEASE_SERVICE_VERSION_MAJOR/MINOR/MICRO "X")` variables.

Repos that don't follow these patterns (third-party libraries like
QCA, PolkitQt, PulseAudioQt, etc.) are not managed by the bump
scripts.

Each script requires a clean working tree in every repo it touches.
Repos with uncommitted changes are skipped with a warning.

build and system installation:
------------------------------

Build into the solution's local prefix (the default remains `build/`):

    ./build-all --parallel 8

The meta-build schedules one package at a time so nested Ninja processes cannot
multiply the requested concurrency. `--parallel N` controls compilation inside
that package. Without it, the builder chooses a conservative value from available
memory (roughly one job per 2 GiB after reserving 1 GiB, capped at eight). Set
`SONICDE_BUILD_JOBS=N` for a persistent override.

Build the full meta-build, then install its CMake packages to `/usr`:

    ./build-all --install --parallel 8

Select a different final destination with:

    ./build-all --install --install-prefix /opt/sonicde --parallel 8

With `--install`, packages are configured for the final destination, while every
dependency installation during the build is redirected with `DESTDIR` under
`state/install-stage/`. Later packages discover these staged dependencies.
**No live-system installation starts until the entire meta-build succeeds.**
If validation, configuration or any build fails, deployment is not run.
Partial-target builds are not accepted by this wrapper.

After the full build succeeds, the wrapper runs `sudo -v` to authenticate
(prompting for a password when required by sudo policy), then runs only the
installation helper under sudo. Cancelling or failing authentication prevents
deployment. Configuration and compilation are not elevated. If deployment
needs to be retried after a successful `--install` build, authenticate and run
the deployment-only step from the workspace:

    sudo -v
    sudo -- python3 scripts/install.py -model state/model.json

Use that helper only with the model from the completed system-prefix build. It
checks all package installation scripts and configured prefixes before starting,
skips system/no-build packages, and stops on the first deployment error.
Deployment is not transactional: an install failure can leave earlier packages
installed, although no packages are deployed while the meta-build is incomplete.

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
