Plan written to `planner/PLAN.md`. 

**Summary:** Rename `sudo_prefix()` → `become_prefix()` and dispatch on `become_method` to support `sudo`, `su`, and `doas`. Raise `ValueError` for unsupported methods. Update all 10 call sites and tests. Small, well-scoped change — high confidence.

[Committed changes to planner branch]