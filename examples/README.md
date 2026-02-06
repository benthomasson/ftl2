# FTL2 Examples

This directory contains practical examples demonstrating FTL2's capabilities, from basic local execution to complex multi-host deployments.

## Overview

FTL2 (Faster Than Light 2) is a Python automation framework similar to Ansible, designed for executing modules on local and remote systems via SSH. These examples show how to use FTL2 effectively.

## Prerequisites

Before running any examples, ensure you have:

1. **FTL2 Installed**
   ```bash
   # From the project root
   cd /path/to/faster-than-light2
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

2. **Docker/Colima Running** (for remote examples)
   ```bash
   # macOS with Colima
   brew install colima docker docker-compose
   colima start

   # Verify Docker is running
   docker info
   ```

3. **Python 3.11+**
   ```bash
   python3 --version
   ```

## Examples Directory Structure

```
examples/
├── README.md                    # This file
├── 01-local-execution/          # Local module execution (no SSH)
│   ├── README.md
│   ├── inventory.yml
│   └── run_examples.sh
├── 02-remote-ssh/               # Single remote host via SSH
│   ├── README.md
│   ├── docker-compose.yml
│   ├── inventory.yml
│   ├── setup.sh
│   └── run_examples.sh
├── 03-multi-host/               # Multiple hosts with groups
│   ├── README.md
│   ├── docker-compose.yml
│   ├── inventory.yml
│   ├── setup.sh
│   └── run_examples.sh
├── 04-ftl-modules/              # FTL modules (fast in-process execution)
│   ├── README.md
│   ├── docker-compose.yml
│   └── example_*.py
└── 05-event-streaming/          # Real-time progress and event streaming
    ├── README.md
    ├── docker-compose.yml
    ├── example_streaming.py
    └── example_remote_streaming.py
```

## Example Progression

### 1. Local Execution (Start Here)

**Directory:** `01-local-execution/`

**What it teaches:**
- Basic FTL2 command structure
- Module execution on localhost
- Inventory configuration
- Common modules (ping, setup, file, shell, copy)

**Run it:**
```bash
cd 01-local-execution
./run_examples.sh
```

**Difficulty:** ⭐ Beginner
**Duration:** 5 minutes
**Prerequisites:** FTL2 installed

---

### 2. Remote SSH Execution

**Directory:** `02-remote-ssh/`

**What it teaches:**
- SSH connection configuration
- Remote module execution
- Gate zipapp mechanism
- Password and key authentication
- Docker-based test environments

**Run it:**
```bash
cd 02-remote-ssh
./setup.sh start
./run_examples.sh
./setup.sh stop
```

**Difficulty:** ⭐⭐ Intermediate
**Duration:** 10 minutes
**Prerequisites:** FTL2 installed, Docker running

---

### 3. Multi-Host Execution

**Directory:** `03-multi-host/`

**What it teaches:**
- Managing multiple hosts
- Host grouping (webservers, databases)
- Parallel execution
- Targeted deployment
- Pattern matching and filtering
- Group variables

**Run it:**
```bash
cd 03-multi-host
./setup.sh start
./run_examples.sh
./setup.sh stop
```

**Difficulty:** ⭐⭐⭐ Advanced
**Duration:** 15 minutes
**Prerequisites:** FTL2 installed, Docker running

---

### 4. FTL Modules

**Directory:** `04-ftl-modules/`

**What it teaches:**
- FTL modules (in-process Python, 250x faster)
- Direct function calls vs subprocess execution
- Remote execution with async SSH
- Bundle building and caching

**Run it:**
```bash
cd 04-ftl-modules
uv run python example_local.py
docker-compose up -d && uv run python example_remote.py
docker-compose down
```

**Difficulty:** ⭐⭐ Intermediate
**Duration:** 10 minutes
**Prerequisites:** FTL2 installed, Docker for remote examples

---

### 5. Event Streaming

**Directory:** `05-event-streaming/`

**What it teaches:**
- Real-time progress events from modules
- Rich progress bar displays
- Multi-host progress tracking
- Event callbacks and result events

**Run it:**
```bash
cd 05-event-streaming
uv run python example_streaming.py
docker-compose up -d && uv run python example_remote_streaming.py
docker-compose down
```

**Difficulty:** ⭐⭐ Intermediate
**Duration:** 10 minutes
**Prerequisites:** FTL2 installed, Docker for remote examples

---

## Quick Start Guide

### First Time Setup

```bash
# 1. Install FTL2
cd /path/to/faster-than-light2
python -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Verify installation
ftl2 --version

# 3. Run first example
cd examples/01-local-execution
./run_examples.sh
```

### Running Remote Examples (with Docker)

```bash
# 1. Start Docker/Colima
colima start  # macOS, or start Docker Desktop

# 2. Run remote example
cd examples/02-remote-ssh
./setup.sh start
./run_examples.sh
./setup.sh stop
```

### Running Multi-Host Example

```bash
cd examples/03-multi-host
./setup.sh start
./run_examples.sh

# Keep containers running for experimentation
# Stop when done:
./setup.sh stop
```

## Learning Path

We recommend following this progression:

1. **Start Local** - Run `01-local-execution` to understand basics
2. **Add SSH** - Run `02-remote-ssh` to learn remote execution
3. **Scale Up** - Run `03-multi-host` to learn parallel operations
4. **Experiment** - Modify inventories, try different modules
5. **Build Real** - Apply learnings to your infrastructure

## Common Commands

### FTL2 Basics

```bash
# Ping hosts
ftl2 -m ping -i inventory.yml

# Gather system facts
ftl2 -m setup -i inventory.yml

# Run shell command
ftl2 -m shell -i inventory.yml -a "cmd='whoami'"

# Create file
ftl2 -m file -i inventory.yml -a "path=/tmp/test state=touch"

# Copy file
ftl2 -m copy -i inventory.yml -a "src=/local/file dest=/remote/file"
```

### Targeting Specific Hosts

```bash
# Target all hosts (default)
ftl2 -m ping -i inventory.yml

# Target specific group
ftl2 -m ping -i inventory.yml --limit webservers

# Target specific host
ftl2 -m ping -i inventory.yml --limit web01

# Pattern matching
ftl2 -m ping -i inventory.yml --limit "web*"

# Exclude hosts
ftl2 -m ping -i inventory.yml --limit "!databases"
```

### Docker Container Management

```bash
# Start containers
./setup.sh start

# Check status
./setup.sh status

# View logs
./setup.sh logs

# Stop containers
./setup.sh stop

# Restart containers
./setup.sh restart
```

## Troubleshooting

### "ftl2: command not found"

```bash
# Ensure virtual environment is activated
source /path/to/faster-than-light2/.venv/bin/activate

# Verify installation
pip list | grep ftl2
```

### "Docker daemon not running"

```bash
# macOS with Colima
colima start

# Verify Docker works
docker info
```

### "Connection refused" on SSH examples

```bash
# Wait a few seconds for containers to start
sleep 10

# Check container status
cd examples/02-remote-ssh  # or 03-multi-host
./setup.sh status

# Restart if needed
./setup.sh restart
```

### "Module not found"

```bash
# Ensure you're in the example directory
cd examples/01-local-execution  # or appropriate example
pwd

# Run from the correct directory
./run_examples.sh
```

## Understanding the Output

### Success

```
web01 | SUCCESS => {
    "changed": false,
    "ping": "pong"
}
```

- **Host**: `web01` - Which host produced this result
- **Status**: `SUCCESS` - Module executed successfully
- **Result**: JSON object with module output
- **changed**: `false` - No changes made to the system

### Failure

```
web01 | FAILED => {
    "error": "Connection timeout",
    "rc": 1
}
```

- **Status**: `FAILED` - Module execution failed
- **error**: Description of what went wrong
- **rc**: Return code (non-zero indicates failure)

## Next Steps

After completing these examples:

1. **Explore Modules** - Check available FTL2 modules in `src/ftl2/modules/`
2. **Read Documentation** - Review main README and module docs
3. **Build Inventories** - Create inventories for your infrastructure
4. **Write Modules** - Create custom modules for your needs
5. **Automate Tasks** - Use FTL2 for real automation workflows

## Additional Resources

- **Project README**: `../README.md` - Main project documentation
- **Module Development**: See existing modules in `../src/ftl2/modules/`
- **Testing**: `../tests/` - Test examples and patterns
- **Gate Debugging**: Review SSH integration tests for advanced debugging

## Contributing

Found an issue or want to add an example?

1. Create an issue on GitHub
2. Submit a pull request with improvements
3. Share your use cases and examples

## Security Warning

⚠️ **These examples use simplified security for learning purposes:**

- Password authentication (not recommended for production)
- Disabled host key checking (dangerous in production)
- Shared credentials across hosts (never do this in production)

**For production use:**

- Use SSH key authentication
- Enable strict host key checking
- Use unique credentials per host
- Implement secrets management (Vault, etc.)
- Enable audit logging
- Use SSH bastion hosts for internal networks

## License

These examples are part of the FTL2 project and use the same license.

## Questions?

If you have questions or need help:

1. Review the README in each example directory
2. Check the main project documentation
3. Open an issue on GitHub
4. Review test files for additional patterns

Happy automating with FTL2! 🚀
