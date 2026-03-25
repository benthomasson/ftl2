Plan written. The bug is on `src/ftl2/retry.py:287` — `failed_hosts / total_hosts` with no zero guard. Fix is a one-liner: `if total_hosts == 0: return False` before the division.

[Committed changes to planner branch]