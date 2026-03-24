# Plan (Iteration 1)

Task: ## Policy engine is implemented but dormant: no tests, no config files, no confirmed integration

## Problem

The policy engine is fully implemented in code but effectively dormant:

- **No unit tests** for policy evaluation
- **No YAML policy files** in the repository
- **No confirmed integration point** outside policy.py

The engine exists as a feature in code but not in practice.

## Impact

This gates the following derived beliefs:

- policy-engine-operational (currently OUT)
- ai-guardrails-fully-operational (currently OUT — blocked by this + ssh-security-gaps)

## Resolution

- Add unit tests for policy evaluation logic
- Add example/default policy YAML files
- Confirm and document the integration point where policies are evaluated before module execution

Resolving this would restore policy-engine-operational to IN and contribute to unblocking ai-guardrails-fully-operational.

---
*Filed from ftl2-expert belief: policy-engine-incomplete*

Closes #2

IMPORTANT - EFFORT LEVEL: MINIMAL
Keep plan VERY brief (2-3 paragraphs max). Focus only on algorithm choice. Skip architectural discussions and detailed analysis.

Plan written to `planner/PLAN.md`. 

**Summary**: The policy engine is already implemented and integrated — this is a "trust but verify" task. Three deliverables:

1. **`tests/test_policy.py`** — unit tests for evaluation logic (match conditions, AND logic, fnmatch patterns, param matching, rule ordering, YAML loading)
2. **`examples/policies/`** — two example YAML policy files (production guardrails + permissive dev)
3. **Document the confirmed integration** in the PR description (code at `context.py:389-390` and `context.py:438-455` already works)

Confidence: **HIGH** — straightforward test-and-document task for clean, self-contained code.

[Committed changes to planner branch]