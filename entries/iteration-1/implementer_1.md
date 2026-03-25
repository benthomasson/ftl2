Done. Two changes made:

1. **`src/ftl2/retry.py:284`** — Added `if total_hosts == 0: return False` before the `min_hosts` guard, preventing `ZeroDivisionError` at line 289.
2. **`tests/test_cli.py:1159`** — Added `test_circuit_breaker_zero_hosts` test with `min_hosts=0` confirming `check_circuit_breaker(0, 0, config)` returns `False` without raising.

[Committed changes to implementer branch]