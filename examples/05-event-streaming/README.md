# Event Streaming Examples

This directory demonstrates FTL2's event streaming system for real-time progress reporting during module execution.

## Overview

FTL2 modules can emit events (progress, log, data) during execution. These events stream in real-time to callers, enabling:

- Progress bars for file transfers and downloads
- Live log messages during operations
- Multi-host progress tracking
- Rich terminal UI for CLI tools

## Examples

| Example | Description |
|---------|-------------|
| `example_streaming.py` | Local streaming with various display options |
| `example_remote_streaming.py` | Remote SSH streaming with progress bars |

## Quick Start

```bash
# Run local streaming examples (no setup required)
uv run python example_streaming.py

# Run remote streaming examples (requires Docker)
docker-compose up -d
uv run python example_remote_streaming.py
docker-compose down
```

## Event Types

### Progress Events

```python
from ftl2.events import emit_progress

emit_progress(
    percent=50,
    message="Copying file",
    current=512000,      # Optional: current bytes/items
    total=1024000,       # Optional: total bytes/items
    task_id="copy-1",    # Optional: for multi-task tracking
)
```

### Log Events

```python
from ftl2.events import emit_log

emit_log("Starting download", level="info")
emit_log("Connection slow, retrying...", level="warning")
emit_log("Download failed!", level="error")
```

### Data Events

```python
from ftl2.events import emit_data

emit_data("Command output line\n", stream="stdout")
```

## Display Options

### Rich Progress Display

Full-featured progress bars with Rich library:

```python
from ftl2.progress import EventProgressDisplay
from ftl2.module_loading.executor import execute_local_streaming

display = EventProgressDisplay()
with display:
    result = await execute_local_streaming(
        module_path,
        params,
        event_callback=display.handle_event,
    )
```

Output:
```
⠋ Copying backup.tar.gz ━━━━━━━━━━━━━━━━━━━━ 45% 0:00:02
```

### Simple Text Display

Lightweight text-based display (no Rich dependency):

```python
from ftl2.progress import SimpleEventDisplay

display = SimpleEventDisplay()
result = await execute_local_streaming(
    module_path,
    params,
    event_callback=display.handle_event,
)
```

Output:
```
Copying file: 0%
Copying file: 50%
Copying file: 100%
[INFO] Transfer complete
```

### Custom Callback

Handle events directly:

```python
def on_event(event):
    if event["event"] == "progress":
        print(f"Progress: {event['percent']}%")
    elif event["event"] == "log":
        print(f"[{event['level']}] {event['message']}")

result = await execute_local_streaming(
    module_path,
    params,
    event_callback=on_event,
)
```

## Multi-Host Progress

Track progress across multiple hosts:

```python
from ftl2.progress import EventProgressDisplay

display = EventProgressDisplay()

async def run_on_host(host):
    callback = display.make_callback(host.name)
    return await execute_remote_streaming(
        host, bundle_path, params,
        event_callback=callback,
    )

with display:
    results = await asyncio.gather(*[
        run_on_host(h) for h in hosts
    ])
```

Output:
```
⠋ [web-01] Deploying ━━━━━━━━━━━━━━━ 75% 0:00:01
⠋ [web-02] Deploying ━━━━━━━━━━━━━   60% 0:00:01
⠋ [db-01] Deploying  ━━━━━━━━━━━━━━━━ 80% 0:00:01
```

## Events in Result

Events are always captured in the result, even without a callback:

```python
result = await execute_local_streaming(module_path, params)

print(f"Events captured: {len(result.events)}")
for event in result.events:
    print(event)
```

## FTL Modules with Events

FTL modules have built-in event support:

```python
from ftl2.ftl_modules import ftl_copy, ftl_get_url

# ftl_copy emits progress during chunked file transfer
result = ftl_copy(src="/large/file", dest="/backup/file")

# ftl_get_url emits progress during download
result = await ftl_get_url(url="https://example.com/file.zip", dest="/tmp/")
```

## Bundled Ansible Modules

Ansible modules in bundles can import and use FTL2 events:

```python
# In your Ansible module
from ftl2.events import emit_progress, emit_log

def main():
    module = AnsibleModule(argument_spec={})

    emit_log("Starting operation", level="info")

    for i, item in enumerate(items):
        process(item)
        emit_progress(
            percent=int((i + 1) * 100 / len(items)),
            message="Processing items",
        )

    module.exit_json(changed=True)
```

## API Reference

### Streaming Executor Functions

```python
# Local streaming
result = await execute_local_streaming(
    module_path: Path,
    params: dict,
    timeout: int = 300,
    check_mode: bool = False,
    event_callback: Callable[[dict], None] | None = None,
) -> ExecutionResult

# Remote streaming (bundle already staged)
result = await execute_remote_streaming(
    host: RemoteHost,
    bundle_path: str,
    params: dict,
    timeout: int = 300,
    check_mode: bool = False,
    event_callback: Callable[[dict], None] | None = None,
) -> ExecutionResult

# Remote streaming with auto-staging
result = await execute_remote_with_staging_streaming(
    host: RemoteHost,
    bundle: Bundle,
    params: dict,
    timeout: int = 300,
    check_mode: bool = False,
    event_callback: Callable[[dict], None] | None = None,
) -> ExecutionResult
```

### Event Emission Functions

```python
from ftl2.events import emit_progress, emit_log, emit_data

emit_progress(percent, message="", current=None, total=None, task_id=None)
emit_log(message, level="info")
emit_data(data, stream="stdout")
```

### Progress Display Classes

```python
from ftl2.progress import EventProgressDisplay, SimpleEventDisplay

# Rich-based display
display = EventProgressDisplay(
    console=None,           # Optional Rich Console
    show_log_events=True,   # Display log events
    show_data_events=False, # Display data events
)

# Simple text display
display = SimpleEventDisplay(
    output=sys.stderr,      # Output stream
    show_log_events=True,
    show_data_events=False,
)
```
