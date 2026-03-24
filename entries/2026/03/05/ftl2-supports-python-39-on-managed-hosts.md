# FTL2 Supports Python 3.9 on Managed Hosts

**Date:** 2026-03-05
**Time:** 09:32

## Overview

FTL2 now supports Python 3.9 on managed hosts, which means it can manage RHEL 9 systems using the default system Python without requiring any additional Python installation. This is a practical advantage over Ansible, which dropped Python 3.9 support.

## Why This Matters

RHEL 9 ships Python 3.9 as the default system interpreter. Ansible requires Python 3.10+ on managed hosts, which means RHEL 9 targets need python3.11 (or newer) installed before Ansible can connect — adding a bootstrap step to every new system.

FTL2 gate code now uses `from __future__ import annotations` so that Python 3.10+ type syntax (`dict[str, Any] | None`, `str | None`, etc.) is stored as strings and never evaluated at runtime. The gate runs cleanly on Python 3.9.

## Practical Impact

- **Zero bootstrap**: FTL2 can manage a fresh RHEL 9 system immediately — no need to install python3.11 first
- **Simpler provisioning**: No chicken-and-egg problem where you need automation to install the Python version your automation tool requires
- **Broader compatibility**: Any system with Python 3.9+ is a valid FTL2 target

## Related

- entries/2026/03/05/ftl2-improvements-for-rhel-9-compatibility.md — implementation details of the fix
