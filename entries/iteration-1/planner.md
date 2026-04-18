Plan written to `workspaces/issue-106/planner/PLAN.md`. 

The change is surgical: add an `expand_host_range()` function and call it in the one loop at `inventory.py:166-169` where YAML host names are iterated. Numeric ranges preserve leading zeros via `zfill`, alpha ranges use `ord()`/`chr()`, and multiple brackets expand as a cartesian product. All host vars from the pattern key propagate to every expanded host.

[Committed changes to planner branch]