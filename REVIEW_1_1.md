# Review (Iteration 1, Attempt 1)

Review complete. Here's the summary:

**Two bugs found:**

1. **`gate_deploy` doesn't register the SSH subsystem** unless the context was created with `gate_subsystem=True`. The method's entire purpose is to deploy a permanent gate as a subsystem, but it delegates to `_get_or_create_gate` which checks `self._gate_subsystem` — so without that flag, it just creates a normal ephemeral gate.

2. **All lifecycle methods use the wrong cache key.** They call `gate_cache_key(host.name)` without the `BecomeConfig`, but gates are stored with `gate_cache_key(host.name, become)`. For any host using privilege escalation, the lookup misses and the methods fail with "No active gate connection."

Both issues were flagged by the implementer in their review notes but left unfixed. The fixes are straightforward (a few lines each).

**Verdict: NEEDS_CHANGES**

Full review written to `workspaces/issue-68-v2/reviewer/REVIEW.md`.

[Committed changes to reviewer branch]