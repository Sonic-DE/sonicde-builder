## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Building and installing

- **Building against freshly built packages needs no install command.** Every `./build-all` and
  `./build-repo <repo>` (without `--install`) configures packages for `/usr` (`--prefix PATH` overrides) and runs each
  package's build as `cmake --build <dir> --target install` with `DESTDIR` into the private staging tree
  `state/install-stage`, so packages land in `state/install-stage/usr`. Every package is configured with
  `CMAKE_PREFIX_PATH` pointing there, so a dependent built afterwards finds the new package (e.g.
  `state/install-stage/usr/lib64/cmake/SonicLogging/`).
- Tools that take a builder prefix (e.g. sonic-terminal's `tools/parity`) get `<builder>/state/install-stage/usr`.
- The "No install step" line in the build output is only ExternalProject's empty separate step.
- A package must be in the solution `build:` list (`config/sonicde/solutions/sonicde.yaml`) for `build-repo` to
  select it.
- `./install` deploys a completed build to the system with `sudo`; it and `--install` are for the user to run, not
  agents. Never reach for `DESTDIR=… cmake --install` or CMake build-tree exports to make one package visible to
  another.
