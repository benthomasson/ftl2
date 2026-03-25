Plan written. The fix is a one-line early return `if total_hosts == 0: return False` before the division at `retry.py:287`, plus a test for the `total_hosts=0` edge case.

[Committed changes to planner branch]