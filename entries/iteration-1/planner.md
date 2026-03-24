Plan written to `planner/PLAN.md`. 

**Summary**: The policy engine is already implemented and integrated — this is a "trust but verify" task. Three deliverables:

1. **`tests/test_policy.py`** — unit tests for evaluation logic (match conditions, AND logic, fnmatch patterns, param matching, rule ordering, YAML loading)
2. **`examples/policies/`** — two example YAML policy files (production guardrails + permissive dev)
3. **Document the confirmed integration** in the PR description (code at `context.py:389-390` and `context.py:438-455` already works)

Confidence: **HIGH** — straightforward test-and-document task for clean, self-contained code.

[Committed changes to planner branch]