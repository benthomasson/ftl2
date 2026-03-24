## TEST CASES

All **13 tests pass** in `tests/test_policy.py`. Coverage includes: empty policy, deny match/no-match, allow-rule skipping, AND logic, fnmatch patterns, param matching, unknown keys, first-deny-wins, YAML loading, and PolicyDeniedError.

Both example YAML policies (`production-guardrails.yaml`, `development-permissive.yaml`) load and evaluate correctly.

## USAGE INSTRUCTIONS FOR USER

**Run tests:**
```bash
cd /Users/ben/git/beliefs-pi/workspaces/issue-2
.venv/bin/python -m pytest tests/test_policy.py -v -o "addopts="
```

**Use the policy engine:**
```python
# Via AutomationContext (automatic enforcement)
ctx = AutomationContext(policy="examples/policies/production-guardrails.yaml", environment="prod")

# Or directly
from ftl2.policy import Policy
policy = Policy.from_file("examples/policies/production-guardrails.yaml")
result = policy.evaluate("shell", {}, environment="prod")
# result.permitted = False, result.reason = "Use proper modules instead of shell in production"
```

Integration is confirmed at `context.py:822` (local) and `context.py:1150` (remote).

## SELF-REVIEW

1. **Easy**: Pure logic, no I/O deps, fast tests.
2. **Nothing missing** from the implementation.
3. **Minor note**: `pytest-cov` not in dev deps requires the `-o "addopts="` workaround.
4. **Flagged by self-review**: `development-permissive.yaml` exact-match for `rm -rf /*` is trivially bypassable — fine as example, not real security.

## Verdict

STATUS: TESTS_PASSED
OPEN_ISSUES: none

[Committed changes to tester branch]