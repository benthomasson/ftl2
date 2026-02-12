# FTL2 Development History

## Foundation (Feb 5)

Core architecture: typed dataclasses for host/inventory config, Strategy-pattern runner (local vs remote), gate communication protocol for remote execution, variable reference system, argument merging, CLI with subcommands.

## FTL Modules (Feb 6)

In-process Ansible module execution — the core performance win. FQCN parser resolves `community.general.slack` to actual module files. Dependency detector finds Python requirements from module DOCUMENTATION strings. Bundle builder packages modules + module_utils into self-contained zipapps. Async executor runs modules as Python functions instead of subprocesses. 3-17x faster than `ansible-playbook`.

## Event Streaming (Feb 6)

Real-time event protocol between gate and controller. Event types: module start/complete, file changes (inotify), system metrics (CPU/memory/disk). Rich TUI for progress display. `await ftl.listen()` for persistent monitoring.

## Automation Context API (Feb 6-7)

The developer-facing interface:

```python
async with automation(inventory="hosts.yml", secret_bindings={...}) as ftl:
    await ftl.file(path="/tmp/test", state="directory")
    await ftl.run_on("webservers", "dnf", name="nginx", state="present")
```

Features: secret bindings (auto-inject credentials, never visible in logs), host-scoped proxies (`ftl["hostname"].module()`), bracket notation for host names with dashes, `add_host()` for dynamic provisioning workflows, check mode, fail_fast, per-host summary printing.

## Gate System (Feb 7)

Remote execution via SSH gate process. Gate builder creates cached zipapps with baked-in modules. FTL modules sent by name (gate has them), Ansible modules sent as bundles. Gate protocol supports debug commands (Info, ListModules). SSH subsystem registration eliminates shell startup overhead. Gates cached in `~/.ftl` per user.

## Native Modules (Feb 7-8)

FTL-native implementations for hot-path modules: `copy` (with file transfer), `template`, `fetch`, `shell`, `swap`, `ping`, `wait_for`. Native modules skip the Ansible module machinery entirely.

## State Management (Feb 7)

State file (`.ftl2-state.json`) tracks dynamically provisioned hosts and resources. `add_host()` persists immediately. Hosts loaded from state on context enter — enables crash recovery and idempotent provisioning. State exposed to automation scripts via `ftl.state`.

## Dependency Management (Feb 7)

`auto_install_deps` installs missing Python packages with `uv` at runtime. `record_deps` captures requirements during execution and writes to `.ftl2-deps.txt`. Module names tracked in `.ftl2-modules.txt` for gate building.

## Audit & Replay (Feb 8)

`record="audit.json"` captures every module execution with timestamps, durations, parameters (secrets redacted), and results. `replay="audit.json"` skips successful actions from a previous run, resuming from the first failure. Enables crash recovery without re-running completed work.

## Policy Engine (Feb 11)

YAML-based rules evaluated before every module execution. Match conditions: `module` (fnmatch), `host`, `environment`, `param.<name>`. First matching deny rule raises `PolicyDeniedError`. Integrated into both `execute()` (local) and `_execute_on_host()` (remote). `Policy.empty()` for backward compatibility.

## JSON Inventory & Inventory Scripts (Feb 11)

`load_inventory()` auto-detects format: executable scripts (run with `--list`), JSON (Ansible `ansible-inventory --list` format with `_meta.hostvars`), or YAML. Enables dynamic inventory from cloud providers without reimplementing inventory plugins — just shell out to `ansible-inventory` and pass the JSON to FTL2.

## Vault Secrets (Feb 11)

HashiCorp Vault KV v2 support via `vault_secrets` parameter. Maps names to `path#field` references, resolved at context startup, accessible via `ftl.secrets["NAME"]` alongside env var secrets. Uses standard `VAULT_ADDR`/`VAULT_TOKEN` env vars. Reads grouped by path to minimize API calls. `hvac` is an optional dependency (`pip install ftl2[vault]`). Works with `secret_bindings` for auto-injection of vault-sourced credentials.
