# Plan (Iteration 1)

Task: ## Support host range patterns in inventory files

## Problem

FTL2 does not support host range patterns in inventory files.

## Expected Behavior

Support Ansible host range syntax in both YAML and INI inventory formats:

### Numeric ranges
```yaml
webservers:
  hosts:
    www[01:50].example.com:      # www01 through www50 (inclusive)
```

### Numeric ranges with stride
```yaml
webservers:
  hosts:
    www[01:50:2].example.com:    # www01, www03, www05, ..., www49
```

### Alphabetic ranges
```yaml
databases:
  hosts:
    db-[a:f].example.com:        # db-a through db-f
```

### Leading zeros
Numeric ranges preserve leading zeros from the start value. `www[01:50]` produces `www01` through `www50`, not `www1` through `www50`.

## Why This Matters

Host ranges are commonly used in production inventories that manage many similar hosts (web server farms, database clusters, etc.). The porting flywheel needs to handle these without manual expansion.


Closes #106

IMPORTANT - EFFORT LEVEL: MINIMAL
Keep plan VERY brief (2-3 paragraphs max). Focus only on algorithm choice. Skip architectural discussions and detailed analysis.

Plan written to `workspaces/issue-106/planner/PLAN.md`. 

The change is surgical: add an `expand_host_range()` function and call it in the one loop at `inventory.py:166-169` where YAML host names are iterated. Numeric ranges preserve leading zeros via `zfill`, alpha ranges use `ord()`/`chr()`, and multiple brackets expand as a cartesian product. All host vars from the pattern key propagate to every expanded host.

[Committed changes to planner branch]