# Diff: main

**Date:** 2026-03-24
**Time:** 15:08



# SSH Security Hardening

## Summary

This PR closes three SSH security gaps: it stops disabling host key verification (which left connections vulnerable to MITM attacks), adds `shlex.quote()` to all shell commands that interpolate user-controlled strings (preventing command injection), and fixes the connection pool key to include credentials (preventing credential confusion where different users could share a connection authenticated as someone else).

## Motivation

The commit message — "Fix SSH security gaps: host key verification, command injection, pool keying" — tells the story clearly. The original code took shortcuts common in early-stage automation tools: `known_hosts=None` everywhere to avoid host key prompts, unquoted string interpolation in shell commands, and a naive pool key that only considered `(hostname, port, username)`. These are fine for a prototype but become real attack surfaces in production, especially for a tool that runs with elevated privileges on remote hosts.

## File-by-File Breakdown

### `src/ftl2/ssh.py`
The core of the fix, with three distinct changes:

1. **Command injection prevention**: Added `import shlex` and wrapped all user-supplied arguments in `shlex.quote()` — in `has_file()`, `path_exists()`, `write_file()` (the `chmod` call), and `chown()`/`chgrp()`. Without this, a path like `/tmp/$(rm -rf /)` or `/tmp/test; cat /etc/shadow` would execute arbitrary commands.

2. **Host key verification restored**: Removed `known_hosts=None` from `ssh_run()` and changed the `SSHHost` default. `None` in asyncssh means "skip all verification." The new default `()` (empty tuple) tells asyncssh to use the system's `~/.ssh/known_hosts`, which is the secure default — connections to unknown hosts will fail, as they should.

3. **Connection pool keying**: The pool key changed from `(hostname, port, username)` to `(hostname, port, username, password, keys_tuple)`. Previously, if you connected to the same host with different credentials (e.g., different deploy keys for different access levels), you'd silently get back the first connection's auth context. The type annotation also relaxed from `dict[tuple[str, int, str | None], SSHHost]` to `dict[tuple, SSHHost]` to accommodate the variable-length key.

### `src/ftl2/automation/context.py`
Removed `known_hosts=None` from `_get_ssh_connection()`. This was the main automation code path — every remote module execution went through here with host key verification disabled.

### `src/ftl2/cli.py`
Changed `known_hosts=None` to `known_hosts=()` in the `test_ssh` CLI command. Same fix, different code path.

### `src/ftl2/runners.py`
Changed `known_hosts=None` to `known_hosts=()` in `RemoteModuleRunner`. This is the gate deployment path — when FTL2 pushes the `.pyz` bundle to remote hosts.

### `tests/test_ssh.py`
Added a `TestSSHSecurity` class with tests that verify:
- Default `known_hosts` uses system defaults, not `None`
- `shlex.quote` neutralizes shell metacharacters in paths
- Different passwords/keys produce different pool entries
- Same credentials reuse the same pool entry
- `chown`/`chgrp` quote their arguments

## Impact

- **Breaking change for hosts not in `known_hosts`**: Any remote host whose key isn't already in `~/.ssh/known_hosts` will now fail to connect. Users who were relying on the "just connect to anything" behavior will need to either add host keys first (`ssh-keyscan`) or explicitly pass `known_hosts=None` if they understand the risk.
- **Paths with special characters now work safely**: Previously, a filename containing spaces or shell metacharacters could cause silent failures or worse. Now they're properly quoted.
- **Connection pool correctness**: Workflows that connect to the same host with different credentials will no longer share connections incorrectly.

## Risks

- **Host key bootstrapping**: For newly provisioned cloud VMs (a core FTL2 use case), the host key won't be in `known_hosts` yet. Users will need a workflow to accept keys on first connect — the `automation()` context still accepts `known_hosts` as a parameter, but the *default* is now secure. This could be a friction point.
- **Pool key includes password in memory**: The password is now part of the dictionary key, meaning it persists in memory as long as the pool lives. Low risk in practice (the `SSHHost` already stored it), but worth noting.
- **No migration path documented**: The commit doesn't include documentation updates. Users upgrading will hit connection failures without clear guidance on how to add host keys.

## Topics to Explore

- [function] `src/ftl2/ssh.py:SSHHost.connect` — How the asyncssh connection is established and how `known_hosts` flows through to the underlying library
- [file] `src/ftl2/runners.py` — The full remote execution pipeline where gate deployment and module execution happen over SSH
- [general] `asyncssh-known-hosts-semantics` — The difference between `None` (disable verification), `()` (system defaults), and a file path in asyncssh's `known_hosts` parameter
- [function] `src/ftl2/automation/context.py:AutomationContext._get_ssh_connection` — How the automation layer manages SSH connection lifecycle and how users could override `known_hosts` per-host
- [general] `first-connect-trust-on-first-use` — How to handle host key verification for dynamically provisioned infrastructure (TOFU patterns)

## Beliefs

- `known-hosts-default-secure` — `SSHHost` defaults to `known_hosts=()` which uses system known_hosts files; `None` (disable verification) is never used as a default anywhere in the codebase
- `shell-commands-always-quoted` — Every shell command in `SSHHost` that interpolates a variable uses `shlex.quote()` to prevent command injection
- `pool-key-includes-credentials` — `SSHConnectionPool` keys connections by `(hostname, port, username, password, client_keys_tuple)`, so different credentials always produce separate connections
- `automation-context-delegates-known-hosts` — `AutomationContext._get_ssh_connection` does not set `known_hosts` at all, relying on `SSHHost`'s secure default

