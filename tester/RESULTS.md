# Test Results: Policy Engine

## TEST CASES

All 13 tests in `tests/test_policy.py` pass:

```
tests/test_policy.py::TestPolicyEvaluate::test_empty_policy_permits_everything PASSED
tests/test_policy.py::TestPolicyEvaluate::test_single_deny_rule_matches PASSED
tests/test_policy.py::TestPolicyEvaluate::test_deny_rule_no_match_permits PASSED
tests/test_policy.py::TestPolicyEvaluate::test_allow_rules_are_skipped PASSED
tests/test_policy.py::TestPolicyEvaluate::test_multiple_conditions_all_must_match PASSED
tests/test_policy.py::TestPolicyEvaluate::test_fnmatch_patterns PASSED
tests/test_policy.py::TestPolicyEvaluate::test_module_wildcard PASSED
tests/test_policy.py::TestPolicyEvaluate::test_param_matching PASSED
tests/test_policy.py::TestPolicyEvaluate::test_unknown_condition_key_rejects_match PASSED
tests/test_policy.py::TestPolicyEvaluate::test_first_matching_deny_wins PASSED
tests/test_policy.py::TestPolicyFromFile::test_load_yaml PASSED
tests/test_policy.py::TestPolicyFromFile::test_empty_rules_file PASSED
tests/test_policy.py::TestPolicyDeniedError::test_carries_rule PASSED
```

Example YAML policies also verified — both load and evaluate correctly.

### Coverage

| Area | Tests | Status |
|------|-------|--------|
| Empty policy (default permit) | 1 | PASS |
| Single deny match/no-match | 2 | PASS |
| Allow rules skipped (deny-only engine) | 1 | PASS |
| AND logic (multiple conditions) | 1 (3 sub-assertions) | PASS |
| fnmatch patterns (host, module) | 2 | PASS |
| param.* matching | 1 (3 sub-assertions) | PASS |
| Unknown condition key | 1 | PASS |
| First-deny-wins ordering | 1 | PASS |
| YAML loading | 1 | PASS |
| Empty YAML | 1 | PASS |
| PolicyDeniedError | 1 | PASS |

## USAGE INSTRUCTIONS FOR USER

### Running the tests

```bash
cd /Users/ben/git/beliefs-pi/workspaces/issue-2
.venv/bin/python -m pytest tests/test_policy.py -v -o "addopts="
```

The `-o "addopts="` override is needed because `pyproject.toml` configures `--cov` flags that require `pytest-cov` (not installed).

### Using the policy engine

**1. Create a policy YAML file** (see `examples/policies/` for templates):

```yaml
rules:
  - decision: deny
    match:
      module: shell
      environment: prod
    reason: "Use proper modules instead of shell in production"
```

Match keys: `module`, `host`, `environment`, `param.<name>`. All use fnmatch patterns. All conditions in a rule must match (AND logic).

**2. Pass the policy file to AutomationContext:**

```python
ctx = AutomationContext(policy="path/to/policy.yaml", environment="prod")
```

Every module execution will be checked against the policy. A matching deny rule raises `PolicyDeniedError`.

**3. Or use the Policy class directly:**

```python
from ftl2.policy import Policy

policy = Policy.from_file("examples/policies/production-guardrails.yaml")
result = policy.evaluate("shell", {"cmd": "ls"}, host="prod-web-01", environment="prod")
if not result.permitted:
    print(f"Denied: {result.reason}")
```

### Integration points

- `AutomationContext.__init__` loads policy at line 389-390
- `AutomationContext._check_policy()` is called before local execution (line 822) and remote execution (line 1150)

## SELF-REVIEW

1. **Easy to test**: The policy engine is pure logic with no I/O dependencies (except YAML loading). Tests are fast and deterministic.
2. **Nothing missing**: The plan, code, and tests are well-aligned. No gaps found.
3. **Next time**: Having `pytest-cov` in dev dependencies would avoid the `-o "addopts="` workaround.
4. **One observation**: The `development-permissive.yaml` example uses exact string match for `rm -rf /*` which is trivially bypassable (extra spaces, different paths). The self-review already flagged this — it's fine as an example, not as real security.

## Verdict

STATUS: TESTS_PASSED
OPEN_ISSUES: none
