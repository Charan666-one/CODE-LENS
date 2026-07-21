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

- `CONTAINS` and `Repository`/`Module` nodes — module granularity for a flat
  repo is a CP-1.2 decision.
- Metrics (`loc`, `complexity`) — radon-specific, asserted once CP-1.2 lands.
- Non-`resolved` confidence — `tiny_python` is deliberately unambiguous so the
  structural contract stays crisp. A confidence-focused fixture exercising
  `heuristic` / `dynamic_unknown` arrives with **CP-1.3**, where dynamic
  dispatch is the actual subject.

## Status

The comparison test is **skipped** until `app.parser.parse_repository` exists.
It is the test the parser must earn its way to passing in CP-1.2, and the alarm
that fires on any graph drift thereafter. The manifest well-formedness tests run
today and keep these hand-written files internally consistent.
