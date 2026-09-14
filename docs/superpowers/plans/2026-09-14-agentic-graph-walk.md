# Agentic Graph Walk — implementation plan (PR 1: engine, evaluation, report)

Spec: `docs/superpowers/specs/2026-09-14-agentic-graph-walk-design.md`.

Order, each step with its test before the next:

1. `walker/network.py` — `Network`, `build_network(organism, data_dir, mongo_db)`,
   JSON cache with a file signature. Test: synthetic KGML dir → nodes, signs, tags.
2. `walker/heat.py` — hypergeometric heat, degree cap, `scan_graph`, `scan_here`.
   Test: a degree-1 node cannot exceed heat −log10(K/N); a hub with all-relevant
   neighbours is hottest; candidates are non-adjacent.
3. `walker/overlay.py` — job features → r, layers, labels, miRNA regulator nodes.
   Test: fake job objects → r = OR, unlabeled header → columns unlabeled, miRNA node + edge.
4. `walker/walk.py` + `walker/policies.py` — `Walker` with the six tool methods, the SDK
   tool wrappers, the greedy policy. Test: plan refusals, step refusals, edge closes per
   direction, seen ledger, budgets, determinism.
5. `walker/card.py`, `verify.py`, `sense.py`, `narrate.py`, `writer.py`, `record.py` —
   the model stages with deterministic fallbacks; Verifier and narrative checks.
   Test: verify rules on hand-built statements and results.
6. `walker/report.py`, `walker/evaluate.py`, `walker/cli.py` — HTML report (map or
   region layout), planted-module recall, command line. Test: evaluate on the fixture
   returns metrics in [0, 1]; report renders without external assets.
7. Run on the example job (greedy, then model): record + HTML, screenshot in Chrome.
8. Docs page, CHANGELOG, ruff/vulture clean, test sweep, PR, watch Gate + review.
