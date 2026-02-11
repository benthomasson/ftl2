# FTL2

Fast Python automation using the Ansible module ecosystem. 3-17x faster than `ansible-playbook`.

## Install

```bash
uvx --from "git+https://github.com/benthomasson/ftl2" ftl2
```

## Quick Start

```python
import asyncio
from ftl2 import automation

async def main():
    async with automation(
        inventory="inventory.yml",
        fail_fast=True,
    ) as ftl:
        await ftl.webservers.dnf(name="nginx", state="present")
        await ftl.webservers.service(name="nginx", state="started")
        await ftl.webservers.ansible.posix.firewalld(
            port="80/tcp", state="enabled", permanent=True, immediate=True,
        )

asyncio.run(main())
```

## What It Does

FTL2 runs Ansible modules directly from Python without YAML, Jinja2, or the `ansible-playbook` runtime. Modules execute in-process instead of as subprocesses, which is where the speed comes from.

```python
# Any Ansible module works — same names, same parameters
await ftl.local.community.general.linode_v4(label="web01", type="g6-standard-1", ...)
await ftl.webservers.copy(src="app.conf", dest="/etc/nginx/conf.d/app.conf")
await ftl.db.community.postgresql.postgresql_db(name="myapp", state="present")
```

## Features

- **Secret bindings** — inject API tokens into modules automatically, never visible in code or logs
- **State tracking** — `.ftl2-state.json` for idempotent provisioning with crash recovery
- **Policy engine** — YAML-based rules to restrict what actions can be taken per module, host, or environment
- **Audit recording** — JSON trail of every action with timestamps, durations, params
- **Audit replay** — resume from failure by replaying successful actions from a previous run
- **Gate modules** — pre-build remote execution gates with all modules baked in
- **Event streaming** — real-time events from remote hosts (file changes, system metrics)
- **Dynamic hosts** — `add_host()` for provisioning workflows where you create and configure in one script
- **Check mode** — dry-run without executing
- **Auto-install deps** — missing Python packages installed with `uv` at runtime

```python
async with automation(
    inventory="inventory.yml",
    secret_bindings={
        "community.general.linode_v4": {"access_token": "LINODE_TOKEN"},
        "uri": {"bearer_token": "API_TOKEN"},
    },
    state_file=".ftl2-state.json",
    policy="policy.yml",
    environment="prod",
    gate_modules="auto",
    record="audit.json",
    fail_fast=True,
) as ftl:
    ...
```

## Policy Engine

Restrict what actions are permitted based on module, host, environment, and parameters:

```yaml
# policy.yml
rules:
  - decision: deny
    match:
      module: "shell"
      environment: "prod"
    reason: "Use proper modules in production"

  - decision: deny
    match:
      module: "*"
      param.state: "absent"
      host: "prod-*"
    reason: "No destructive actions on production hosts"
```

```python
async with automation(policy="policy.yml", environment="prod") as ftl:
    await ftl.file(path="/tmp/test", state="absent")
    # Raises PolicyDeniedError: No destructive actions on production hosts
```

## Dynamic Provisioning

Create cloud servers and configure them in a single script:

```python
async with automation(
    state_file=".ftl2-state.json",
    secret_bindings={
        "community.general.linode_v4": {"access_token": "LINODE_TOKEN", "root_pass": "ROOT_PASS"},
    },
    fail_fast=True,
) as ftl:
    # Provision
    if not ftl.state.has("web01"):
        server = await ftl.local.community.general.linode_v4(
            label="web01", type="g6-standard-1", region="us-east", image="linode/fedora43",
        )
        ftl.add_host("web01", ansible_host=server["instance"]["ipv4"][0], ansible_user="root")
        await ftl.local.wait_for(host=server["instance"]["ipv4"][0], port=22, timeout=300)

    # Configure immediately
    await ftl["web01"].dnf(name="nginx", state="present")
    await ftl["web01"].service(name="nginx", state="started", enabled=True)
```

## Performance

Benchmarked with [ftl2-performance](https://github.com/benthomasson/ftl2-performance):

| Benchmark | Ansible | FTL2 | Speedup |
|-----------|---------|------|---------|
| file_operations (30 tasks) | 6.17s | 0.43s | **14.2x** |
| template_render (10 tasks) | 3.22s | 0.19s | **16.6x** |
| uri_requests (15 requests) | 3.75s | 0.30s | **12.4x** |
| local_facts (1 task) | 0.73s | 0.22s | **3.3x** |

## Development

```bash
git clone git@github.com:benthomasson/ftl2.git
cd ftl2
uv pip install -e ".[dev]"
pytest
```

## License

Apache-2.0
