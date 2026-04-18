# Plan: Support host range patterns in inventory files

Closes #106

## PLAN

### Requirements

Add support for Ansible-style host range patterns (`[start:end]` and `[start:end:stride]`) in inventory host names. When a host name like `www[01:50].example.com` appears as a key in the YAML inventory `hosts:` dict, expand it into individual hosts `www01.example.com` through `www50.example.com`. Support numeric ranges (with leading-zero preservation), alphabetic ranges, and stride.

### Algorithm

Use a regex to detect `[start:end]` or `[start:end:stride]` patterns in host name strings. For each match, generate the range values, then substitute back into the host name template. Multiple bracket patterns in a single hostname should be supported (Ansible supports this), expanding as a cartesian product.

### Implementation Steps

#### Step 1: Add `expand_host_range` function

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `src/ftl2/inventory.py` | After line 16 (imports), ~line 18 | Add `import re` at top of file |
| `src/ftl2/inventory.py` | After `_host_from_vars` (line 244) | Add new function `expand_host_range(pattern: str) -> list[str]` that: (1) uses regex `r'\[([^\]]+)\]'` to find bracket groups, (2) parses each group as `start:end` or `start:end:stride`, (3) determines if numeric or alpha, (4) for numeric: preserves leading zeros via zfill based on len(start), (5) for alpha: uses `ord()`/`chr()` range, (6) if multiple brackets, produces cartesian product, (7) returns list of expanded hostnames. If no brackets found, return `[pattern]`. |

#### Step 2: Integrate expansion into `_process_group`

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `src/ftl2/inventory.py` | Lines 165-169 | In the host iteration loop, replace direct `host_name` usage with expansion. For each `host_name` key from `group_data["hosts"]`, call `expand_host_range(host_name)`. For each expanded name, call `group.add_host(_host_from_vars(expanded_name, host_data))`. The same `host_data` (vars) dict applies to all expanded hosts. |

Current code (lines 165-169):
```python
if "hosts" in group_data and isinstance(group_data["hosts"], dict):
    for host_name, host_data in group_data["hosts"].items():
        if not isinstance(host_data, dict):
            host_data = {}
        group.add_host(_host_from_vars(host_name, host_data))
```

Should become:
```python
if "hosts" in group_data and isinstance(group_data["hosts"], dict):
    for host_name, host_data in group_data["hosts"].items():
        if not isinstance(host_data, dict):
            host_data = {}
        for expanded_name in expand_host_range(host_name):
            group.add_host(_host_from_vars(expanded_name, host_data))
```

#### Step 3: Add tests

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `tests/test_inventory.py` | End of file | Add `TestHostRangeExpansion` class testing: (1) numeric range `www[01:03].example.com` -> 3 hosts with zero-padding, (2) alphabetic range `db-[a:c].example.com` -> 3 hosts, (3) stride `www[01:10:3].example.com` -> www01, www04, www07, www10, (4) no-bracket passthrough, (5) multiple brackets (cartesian product), (6) integration test loading a YAML inventory file with range patterns and verifying expanded hosts appear in the group. |

### Key Design Decisions

1. **Expansion happens at YAML parse time only** — JSON/script inventories list hosts explicitly, so no expansion needed there. This matches Ansible's behavior.
2. **Host vars from the range pattern key apply to all expanded hosts** — if `www[01:03]:` has `ansible_user: admin`, all three expanded hosts get that var.
3. **Ranges are inclusive** — `[01:03]` produces 01, 02, 03 (matching Ansible).
4. **Leading zeros from start value determine padding width** — `[01:50]` pads to 2 digits, `[001:999]` pads to 3.

### Success Criteria

- `load_inventory` on a YAML file with `www[01:50].example.com` produces 50 individual `HostConfig` entries named `www01.example.com` through `www50.example.com`
- Alphabetic ranges `db-[a:f].example.com` produce 6 hosts
- Stride `www[01:10:2].example.com` produces `www01`, `www03`, `www05`, `www07`, `www09`
- Non-range hostnames pass through unchanged
- All existing inventory tests continue to pass

## SELF-REVIEW

1. **What went well**: The codebase is clean and the insertion point is unambiguous — one loop in `_process_group` handles all YAML host parsing.
2. **Missing info**: I didn't verify whether Ansible treats ranges as inclusive on both ends (I'm assuming yes based on the issue description). The implementer should verify if there are edge cases around single-character numeric ranges like `[1:3]` vs `[01:03]`.
3. **Next time**: Having an example Ansible inventory with range patterns to test against would speed up validation.
