# Task

## Support host range patterns in inventory files

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

Started: 2026-04-18T09:23:56.450979