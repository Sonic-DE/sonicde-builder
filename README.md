SonicDE standalone builder
==========================

This is a standalone builder for [SonicDE](https://github.com/Sonic-DE/)
(fork of KDE with full X11 compatibility) that replaces the
[MPBT](https://github.com/metux/mpbt) workspace with a Python + CMake
superbuild pipeline.

Warning: it's still an early work-in-progress.

howto:
------

* install dependencies: `pip install pyyaml` and a working Qt6 + CMake 3.28+
* just fetch git repos: `./fetch-all`
* full build: `./build-all`
* create dependency graph: `./generate-depgraph`
* run upstream sync (autopick): `./autopick`
* reset repos after merged PRs: `./autopick-success-cleanup`
* cleanup failed autopick runs: `./autopick-fail-cleanup --force`
* reset trackers (recovery): `./reset-trackers --yes`
* check for missing GitHub repos: `./check-new-repos`
* list OS packages to install: `./install-deps`
* merge per-project graphify graphs: `./merge-graphs`

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
    scripts/git-autopick     upstream sync (rebase tracker range onto master)

configuration:
--------------

The `config/sonicde/` tree is imported from the SonicDE meta-build
workspace. The solution file (`solutions/sonicde.yaml`) defines the
build list, install prefix, environment, and fetch refspecs. Package
manifests under `packages/` define Git sources, dependencies, and
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
