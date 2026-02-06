# Automation Context Manager Examples

This directory demonstrates FTL2's automation context manager interface - a clean, AI-friendly way to write automation scripts.

## Overview

The automation context manager provides an intuitive interface:

```python
from ftl2 import automation

async with automation() as ftl:
    await ftl.file(path="/tmp/test", state="directory")
    await ftl.copy(src="config.yml", dest="/etc/app/config.yml")
    await ftl.command(cmd="systemctl restart myapp")
```

## Examples by Phase

| Example | Phase | Description |
|---------|-------|-------------|
| `example_phase1_basic.py` | 1 | Core context manager with ftl.module_name() syntax |

## Quick Start

```bash
# Run Phase 1 examples
uv run python example_phase1_basic.py
```

## Phase 1: Core Context Manager

The foundation - clean module access via attributes:

```python
async with automation() as ftl:
    # Access any module as an attribute
    await ftl.file(path="/tmp/test", state="touch")
    await ftl.copy(src="a.txt", dest="b.txt")
    await ftl.command(cmd="echo hello")
```

### Features

**Module Restriction:**
```python
async with automation(modules=["file", "copy"]) as ftl:
    await ftl.file(...)  # OK
    await ftl.command(...)  # Raises AttributeError
```

**Verbose Mode:**
```python
async with automation(verbose=True) as ftl:
    await ftl.file(path="/tmp/test", state="touch")
    # Output: [file] ok (changed)
```

**Result Tracking:**
```python
async with automation() as ftl:
    await ftl.file(path="/tmp/a", state="touch")
    await ftl.file(path="/tmp/b", state="touch")

    print(f"Executed {len(ftl.results)} modules")
    for r in ftl.results:
        print(f"  {r.module}: success={r.success}")
```

## Why This Interface?

1. **AI-Friendly**: Natural language patterns that AI generates easily
2. **Clean Syntax**: No boilerplate, just `await ftl.module_name()`
3. **250x Faster**: FTL modules run in-process, not as subprocesses
4. **Type-Safe**: Full IDE autocomplete and type checking support
5. **Pythonic**: Uses standard async context managers

## Coming Soon

- Phase 2: Inventory integration (`ftl.hosts`, `ftl.run_on()`)
- Phase 3: Secrets management (`ftl.secrets`)
- Phase 4: Check mode (dry run)
- Phase 5: Progress and output integration
- Phase 6: Error handling (`ftl.failed`, `ftl.errors`)
