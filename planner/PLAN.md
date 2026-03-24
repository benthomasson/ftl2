# Plan: Activate the Dormant Policy Engine

## PLAN

### What and Why

The policy engine (`src/ftl2/policy.py`, ~178 lines) is fully implemented and already integrated into `AutomationContext._check_policy()` at both local (line 822) and remote (line 1150) execution paths. It works — it just has no tests, no example policy files, and therefore no confidence it behaves correctly. Three deliverables activate it:

### Implementation Steps

1. **Add `tests/test_policy.py`** — unit tests for the `Policy` class covering:
   - Empty policy permits everything
   - Single deny rule matches and blocks
   - Multiple conditions (AND logic) — all must match to deny
   - fnmatch patterns (`*`, `prod-*`) work for module, host, environment
   - `param.<name>` matching against module parameters
   - Unknown condition keys cause rule to not match (line 133-135)
   - First matching deny wins (rule ordering)
   - `from_file()` loads YAML correctly
   - `PolicyDeniedError` carries the matching rule

2. **Add `examples/policies/` directory** with two example YAML files:
   - `production-guardrails.yaml` — deny shell in prod, deny destructive actions on prod hosts
   - `development-permissive.yaml` — minimal restrictions (maybe deny `rm -rf /` patterns)

3. **Document the integration** — a brief comment or docstring addition in `context.py` is sufficient. The integration is already confirmed at lines 389-390 (load) and 438-455 (`_check_policy`). No code changes needed here, just acknowledgment in the PR description.

### Key Decisions

- **Test style**: Follow existing test conventions in `tests/` (pytest, see `conftest.py`). Use in-memory `PolicyRule` objects for unit tests; use `tmp_path` fixture for `from_file()` tests.
- **No integration tests needed**: The `_check_policy` call in `context.py` is straightforward delegation. Unit-testing `Policy` in isolation is sufficient.
- **Example files go in `examples/`**, not injected as defaults — policy should be opt-in.

### Success Criteria

- `pytest tests/test_policy.py` passes with full coverage of the evaluation logic
- Example YAML files are valid and loadable via `Policy.from_file()`
- Belief `policy-engine-operational` can be moved to IN
- Issue #2 can be closed

## SELF-REVIEW

1. **What went well**: The code is clean and self-contained (~178 lines, pure logic, no side effects beyond logging). Easy to plan tests for.
2. **Missing info**: I didn't check whether `pytest` is the test runner or if there's a `pyproject.toml` / `Makefile` test command. The existing 35+ test files strongly suggest pytest.
3. **What would help next time**: Having the conftest.py contents would confirm test fixtures/conventions.
4. **Confidence**: **HIGH** — the policy engine is simple, well-structured, and the task is straightforward (add tests + examples for existing working code).
