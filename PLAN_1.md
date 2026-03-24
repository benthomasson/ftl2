# Plan (Iteration 1)

Task: ## become_method field exists but only sudo is implemented

## Problem

BecomeConfig.become_method exists for Ansible compatibility but sudo_prefix() always emits sudo commands regardless of its value. No other escalation method (su, pbrun, doas, etc.) is implemented.

This limits production environments that use alternative privilege escalation methods.

## Impact

This is a known limitation recorded in the ftl2-expert knowledge base as become-method-only-sudo.

## Resolution

Either implement additional escalation methods or document that only sudo is supported and remove/deprecate the become_method field to avoid confusion.

---
*Filed from ftl2-expert belief: become-method-only-sudo*

Closes #4

IMPORTANT - EFFORT LEVEL: MINIMAL
Keep plan VERY brief (2-3 paragraphs max). Focus only on algorithm choice. Skip architectural discussions and detailed analysis.

Plan written to `planner/PLAN.md`. 

**Summary:** Rename `sudo_prefix()` → `become_prefix()` and dispatch on `become_method` to support `sudo`, `su`, and `doas`. Raise `ValueError` for unsupported methods. Update all 10 call sites and tests. Small, well-scoped change — high confidence.

[Committed changes to planner branch]