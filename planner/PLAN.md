# Plan: Implement become_method support beyond sudo

Closes #4

## PLAN

**What:** The `BecomeConfig` dataclass accepts a `become_method` field (default `"sudo"`) but `sudo_prefix()` ignores it — always emitting `sudo -n` commands. Users setting `become_method="su"` or `become_method="doas"` get sudo anyway, which is silently wrong.

**Approach:** Rename `sudo_prefix()` to `become_prefix()` (it's not always sudo anymore) and dispatch on `become_method`. Implement support for the three most common methods: `sudo`, `su`, and `doas`. Raise `ValueError` for unsupported methods so users get a clear error instead of silent wrong behavior.

**Implementation steps:**

1. **`src/ftl2/types.py`** — Rename `sudo_prefix()` → `become_prefix()`. Add method dispatch:
   - `sudo`: `sudo -n [-u USER] CMD` (current behavior)
   - `su`: `su - USER -c 'CMD'` (note: non-interactive, needs appropriate PAM config)
   - `doas`: `doas [-u USER] CMD`
   - Anything else: raise `ValueError(f"Unsupported become_method: {self.become_method}")`
   - Consider adding a `Literal["sudo", "su", "doas"]` type hint for `become_method` instead of bare `str`

2. **All callers** — Update `become_cfg.sudo_prefix(...)` → `become_cfg.become_prefix(...)` in:
   - `src/ftl2/runners.py:869`
   - `src/ftl2/automation/proxy.py` (lines 444, 458, 464, 465, 469, 473, 475, 477, 860)

3. **`tests/test_become.py`** — Update existing `test_sudo_prefix_*` tests to use new method name. Add tests for `su` and `doas` methods, and a test that unsupported methods raise `ValueError`.

**Success criteria:**
- `BecomeConfig(become=True, become_method="sudo").become_prefix("whoami")` → `"sudo -n whoami"`
- `BecomeConfig(become=True, become_method="doas").become_prefix("whoami")` → `"doas whoami"`
- `BecomeConfig(become=True, become_method="su").become_prefix("whoami")` → `"su - root -c 'whoami'"`
- `BecomeConfig(become=True, become_method="pbrun").become_prefix("whoami")` → `ValueError`
- All existing tests pass (with updated method name)

## SELF-REVIEW

1. **What went well:** Clear, small-scoped problem. The code is well-structured — one method to change, clear caller sites.
2. **Missing info:** Whether there are downstream consumers outside this repo that call `sudo_prefix()` directly (breaking change). Whether `su` needs specific flag variations for this project's use cases.
3. **Would help next time:** Knowing the project's compatibility/breaking-change policy.
4. **Confidence: HIGH** — Small, well-defined change with clear before/after behavior. The rename is the riskiest part (breaking callers) but all callers are in-repo.
