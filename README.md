# FTL2

AI-first Python automation using the Ansible module ecosystem. 3-21x faster than `ansible-playbook`.

## Install

```bash
pip install ftl2
```

Or run directly with uvx:

```bash
uvx ftl2 --help
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

FTL2 runs Ansible modules directly from Python without YAML, Jinja2, or the `ansible-playbook` runtime. Common modules (file, copy, shell, command, etc.) have native implementations that execute in-process. Ansible collection modules fall back to subprocess execution. For remote hosts, modules are pre-built into a gate package once, then only JSON parameters are sent over SSH on each call — no re-uploading module code per task. Concurrency uses asyncio instead of Ansible's fork-based parallelism.

```python
# Any Ansible module works — same names, same parameters
await ftl.local.community.general.linode_v4(label="web01", type="g6-standard-1", ...)
await ftl.webservers.copy(src="app.conf", dest="/etc/nginx/conf.d/app.conf")
await ftl.db.community.postgresql.postgresql_db(name="myapp", state="present")
```

## Features

- **Vault secrets** — pull secrets from HashiCorp Vault KV v2 with `vault_secrets={"DB_PW": "myapp#db_password"}`
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
    vault_secrets={
        "DB_PASSWORD": "myapp#db_password",
    },
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
  # IMPORTANT: shell, command, and raw can all execute arbitrary commands.
  # Denying only one leaves the others as bypass routes.
  - decision: deny
    match:
      module: "shell"
      environment: "prod"
    reason: "Use proper modules in production"

  - decision: deny
    match:
      module: "command"
      environment: "prod"
    reason: "Use proper modules in production"

  - decision: deny
    match:
      module: "*.raw"
      environment: "prod"
    reason: "Raw module execution not permitted in production"

  - decision: deny
    match:
      module: "*"
      param.state: "absent"
      host: "prod-*"
    reason: "No destructive actions on production hosts"
```

> **Note:** The `shell`, `command`, and `raw` modules can all execute arbitrary commands.
> A policy that denies only `shell` does not block `command` or `raw` — always deny all three
> together when restricting arbitrary execution. See `examples/policies/` for reference.

```python
async with automation(policy="policy.yml", environment="prod") as ftl:
    await ftl.file(path="/tmp/test", state="absent")
    # Raises PolicyDeniedError: No destructive actions on production hosts
```

## Vault Secrets

Pull secrets from HashiCorp Vault instead of environment variables:

```python
async with automation(
    vault_secrets={
        "DB_PASSWORD": "myapp#db_password",
        "API_KEY": "myapp#api_key",
    },
    secret_bindings={
        "community.general.slack": {"token": "SLACK_TOKEN"},
    },
) as ftl:
    pw = ftl.secrets["DB_PASSWORD"]  # from Vault
```

Uses standard `VAULT_ADDR` and `VAULT_TOKEN` env vars. Install with `pip install ftl2[vault]`.

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

## AI-First Automation

FTL2 is designed for AI agents as the primary user. Traditional automation tools force AI to generate YAML with fragile indentation, parse unstructured error output, and work around DSL limitations. FTL2 eliminates all of that — AI agents write native Python, get structured errors, and leverage the Ansible module ecosystem they already know from training data.

**Why Python over YAML for AI:**

| Problem | Ansible | FTL2 |
|---------|---------|------|
| Syntax errors | YAML indentation (top LLM failure mode) | Python keywords, standard validation |
| Control flow | `when`, `block`, `rescue` in YAML strings | Native `if/else`, `try/except`, loops |
| Error handling | Parse callback output | Structured JSON, full tracebacks |
| Debugging | Read log files | Print statements, Python debugger |
| Composition | Role dependencies, meta/main.yml | Import, call functions |

**Safe AI autonomy:** Secret bindings inject credentials at runtime — AI-generated code never sees actual values, preventing accidental leakage into logs or context windows. Policy engines restrict what modules AI can call. Check mode lets AI dry-run before committing.

```python
# AI agent generates this — secrets stay in the tool layer
async with automation(
    secret_bindings={
        "amazon.aws.*": {"aws_access_key_id": "AWS_KEY"},
        "community.general.slack": {"token": "SLACK_TOKEN"},
    },
    policy="policy.yml",
) as ftl:
    await ftl.webservers.dnf(name="nginx", state="present")
    # Credentials injected automatically, never visible in generated code
```

**AI reconciliation loop:** FTL2 supports observe-decide-act-verify loops where AI replaces hardcoded controller logic. The AI observes current state through modules, reasons about the gap to desired state, emits FTL2 calls to close the gap, then verifies convergence. As recurring problems get solved, AI writes deterministic rules — cost converges toward zero over time.

**Zero onboarding:** AI already knows Ansible module names and parameters from training data. No need for thousands of tool definitions or MCP servers. A minimal CLAUDE.md is enough for an AI agent to start provisioning infrastructure:

```markdown
# CLAUDE.md — Automation

Use ftl2 for all infrastructure automation. Write Python scripts, not YAML.

## Pattern
\```python
from ftl2 import automation
async with automation(
    secret_bindings={"community.general.linode_v4": {"access_token": "LINODE_TOKEN"}},
    state_file=".ftl2-state.json",
    fail_fast=True,
) as ftl:
    await ftl.webservers.dnf(name="nginx", state="present")
\```

## Rules
- Use short names for builtins: `file`, `copy`, `shell`, `service`, `dnf`, `user`
- Use FQCN for collections: `community.general.linode_v4`, `ansible.posix.firewalld`
- Secrets go in `secret_bindings`, never in code
- Use `ftl.local` for API/cloud modules, `ftl.groupname` for remote hosts
- `pip install ftl2` to install
```

## Performance

### Local benchmarks

Benchmarked with [ftl2-performance](https://github.com/benthomasson/ftl2-performance) (single host, localhost):

| Benchmark | Ansible | FTL2 | Speedup |
|-----------|---------|------|---------|
| file_operations (30 tasks) | 6.17s | 0.43s | **14.2x** |
| template_render (10 tasks) | 3.22s | 0.19s | **16.6x** |
| uri_requests (15 requests) | 3.75s | 0.30s | **12.4x** |
| local_facts (1 task) | 0.73s | 0.22s | **3.3x** |

### Scale tests

Benchmarked with [ftl2-scale-tests](https://github.com/benthomasson/ftl2-scale-tests) against real Linode VMs (Fedora 42, us-east). FTL2 uses gate protocol multiplexing and native modules; Ansible uses `--forks N`.

**SSH workloads** (file operations, package install, service setup across remote hosts):

| Hosts | Workload | Ansible | FTL2 | Speedup |
|-------|----------|---------|------|---------|
| 3 | file_operations | 11.41s | 3.25s | **3.5x** |
| 10 | file_operations | 14.04s | 4.03s | **3.5x** |
| 25 | file_operations | 14.95s | 5.03s | **3.0x** |
| 3 | service_setup | 17.16s | 10.40s | **1.7x** |
| 10 | service_setup | 22.80s | 11.40s | **2.0x** |
| 25 | service_setup | 23.59s | 12.76s | **1.8x** |

**API workloads** (20 HTTP requests per host, `connection: local`):

| Hosts | Ansible | FTL2 | Speedup |
|-------|---------|------|---------|
| 1 | 6.0s | 0.3s | **17.8x** |
| 3 | 6.1s | 0.3s | **19.7x** |
| 10 | 7.4s | 0.6s | **13.5x** |
| 25 | 14.7s | 0.7s | **20.9x** |

API calls show the architecture difference most clearly. FTL2 fires all requests concurrently with async httpx. Ansible forks a subprocess per `uri` task, serializing within each host. At 25 hosts (500 total requests), FTL2 finishes in 0.7s vs Ansible's 14.7s.

Ansible also drops hosts as "unreachable" at 25+ hosts even after preflight verification. FTL2's persistent gate connections handle the same hosts without issues.

## Development

```bash
git clone git@github.com:benthomasson/ftl2.git
cd ftl2
uv pip install -e ".[dev]"
pytest
```

## License

Apache-2.0
