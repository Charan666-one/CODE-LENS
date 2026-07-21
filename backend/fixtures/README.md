# Golden fixtures — the parser's contract

> "A `fixtures/` directory of small repos with hand-verified expected graphs.
> Parser changes run against them in CI. **The parser's correctness is
> CodeLens's correctness.**" — ARCHITECTURE.md §2

Each fixture is a directory containing:

```
<fixture_name>/
├── repo/                  # the source the parser reads (parser INPUT, not our code)
└── expected_graph.json    # the hand-verified facts the parser must produce
```

`repo/` is excluded from ruff and mypy in `pyproject.toml` — it is deliberately
small and occasionally odd, and linting it would be a category error.

## Conventions this contract fixes

These are decisions, not accidents. CP-1.2 implements *to* them.

| Thing | Convention | Example |
|---|---|---|
| Node id | `<kind>:<qualified_name>` | `function:calculator.add` |
| Module qname | repo-relative path, `/`→`.`, `.py` stripped | `calculator` |
| Function qname | `<module>.<name>` | `calculator.add` |
| Method qname | `<module>.<Class>.<name>` | `shapes.Rectangle.area` |
| File qname | the repo-relative path, extension kept | `main.py` |
| `IMPORTS` | file → file | `file:shapes.py → file:calculator.py` |
| `CALLS` | function → function | `function:main.main → function:main.area` |
| Module-scope calls | attributed to the **File** node | `file:main.py → function:main.main` |
| Builtins | **not** nodes; no edges to `print`, `range`, … | — |

## What `expected_graph.json` asserts

`asserted_node_kinds` / `asserted_edge_kinds` declare the scope of the contract.
The harness compares **complete sets within those kinds** — an extra or missing
node/edge of an asserted kind is a failure.

Deliberately **not** asserted yet, because the checkpoint that decides them
hasn't run:

- `CONTAINS` and `Repository`/`Module` nodes — asserting the structural spine
  is left to the invariant tests in `tests/test_parser.py`, which check it
  against real repositories rather than a toy one.
- Metrics (`loc`, `complexity`) — radon-specific and checked by unit test.

## The fixtures

| Fixture | Subject |
|---|---|
| `tiny_python` | The structural contract. Deliberately unambiguous: every call resolves statically, so confidence is `resolved` throughout. |
| `dynamic_python` | The confidence ladder (CP-1.3). Every tier appears exactly once — `self.setup()` and `super().run()` are `resolved`; a repo-unique method name is `heuristic`; a name defined by two classes is `dynamic_unknown` to both. `service.py` holds two classes on purpose, so a resolver guessing "the file's only class" fails it. |

## Status

**Live since CP-1.2.** `app.parser.parse_repository` exists, and the comparison
test runs on every commit — it is now the alarm that fires on graph drift.

The manifest-integrity tests run alongside it and check the hand-verification
itself: that ids follow the convention, every edge endpoint exists, and every
cited line really does show the definition or reference it claims.
