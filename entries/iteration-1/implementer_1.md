Done. Three files created:

1. **`tests/test_policy.py`** — 13 tests covering: empty policy, single deny, no-match permit, allow-rule skipping, AND logic (multiple conditions), fnmatch patterns for host/module, `param.*` matching, unknown condition keys, first-deny-wins ordering, YAML loading, empty YAML, and `PolicyDeniedError`.

2. **`examples/policies/production-guardrails.yaml`** — denies shell/command in prod, state=absent on prod hosts, raw modules in prod.

3. **`examples/policies/development-permissive.yaml`** — only denies `rm -rf /*`.

## SELF-REVIEW

1. **What went well**: The policy engine is clean and self-contained — tests map directly to its behavior. No surprises.

2. **What was unclear**: Nothing — the plan was straightforward and the code was well-documented.

3. **What would help next time**: The plan mentioned documenting integration in `context.py` — that's a PR-description task, not an implementation task. Good that the plan flagged "no code changes needed."

4. **Reviewer focus areas**: The `development-permissive.yaml` uses an exact `cmd` match for `rm -rf /*` — fnmatch would match it literally, but a real attacker could trivially bypass this with whitespace variations. This is fine for an example file but shouldn't be relied on as actual security. The production guardrails are more meaningful since they match on module names and environments.