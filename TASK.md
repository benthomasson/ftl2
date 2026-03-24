# Task

## become_method field exists but only sudo is implemented

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

Started: 2026-03-24T05:44:44.334976