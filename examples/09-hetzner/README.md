# Hetzner Cloud Examples

Provision and manage Hetzner Cloud infrastructure using FTL2 with the `hetzner.hcloud` Ansible collection.

## Setup

```bash
# Install Hetzner collection
ansible-galaxy collection install hetzner.hcloud

# Set API token (from Hetzner Cloud Console > API Tokens)
export HCLOUD_TOKEN="your-api-token"

# SSH key for server access (optional, defaults to ~/.ssh/id_ed25519.pub)
export HETZNER_SSH_PUBKEY_FILE="~/.ssh/id_ed25519.pub"
```

## Examples

### Web Stack (`example_hetzner_web_stack.py`)

Provisions a complete web server on Hetzner Cloud:
- SSH key registration
- Private network with subnet (10.0.0.0/16)
- Firewall with SSH/HTTP/HTTPS/ICMP rules
- Cloud server (CX22: 2 vCPU, 4GB RAM, 40GB SSD)
- Network attachment for private connectivity
- Nginx installation and configuration via SSH

```bash
# Dry run
uv run python example_hetzner_web_stack.py --check

# Provision
uv run python example_hetzner_web_stack.py

# Teardown
uv run python example_hetzner_web_stack.py --teardown
```

## How It Works

FTL2 calls Hetzner Ansible modules via FQCN syntax, giving you:

- **Python-native syntax** — `await ftl.hetzner.hcloud.server(name="web01", ...)` instead of YAML
- **Secret bindings** — `HCLOUD_TOKEN` injected automatically, never visible in scripts or logs
- **State tracking** — `.ftl2-state-hetzner.json` tracks provisioned resources for idempotency
- **Provision + configure** — create a server and SSH into it in the same script with `ftl.add_host()`
- **Ordered teardown** — delete resources in reverse dependency order
- **Check mode** — `--check` flag for dry runs

## Available Hetzner Modules

The `hetzner.hcloud` collection provides modules for all Cloud API resources:

| Module | Purpose |
|--------|---------|
| `server` | Create/delete cloud servers |
| `server_info` | Query server details |
| `server_network` | Attach/detach servers to networks |
| `network` | Create/delete networks |
| `subnetwork` | Manage subnets within networks |
| `firewall` | Create/delete firewalls with rules |
| `firewall_resource` | Apply firewalls to servers/labels |
| `ssh_key` | Manage SSH keys |
| `volume` | Create/delete block storage volumes |
| `volume_attachment` | Attach/detach volumes to servers |
| `floating_ip` | Manage floating IPs |
| `primary_ip` | Manage primary IPs |
| `load_balancer` | Create/delete load balancers |
| `load_balancer_service` | Configure LB services (HTTP/TCP) |
| `load_balancer_target` | Add/remove LB targets |
| `load_balancer_network` | Attach LBs to networks |
| `certificate` | Manage TLS certificates |
| `placement_group` | Manage placement groups |
| `rdns` | Configure reverse DNS |
| `zone` | Manage DNS zones |
| `zone_rrset` | Manage DNS record sets |
| `storage_box` | Manage storage boxes |

## Hetzner Cloud Locations

| Code | City | Region |
|------|------|--------|
| `nbg1` | Nuremberg | EU (Germany) |
| `fsn1` | Falkenstein | EU (Germany) |
| `hel1` | Helsinki | EU (Finland) |
| `ash` | Ashburn, VA | US East |
| `hil` | Hillsboro, OR | US West |
| `sin` | Singapore | Asia-Pacific |

## Server Types

| Prefix | Type | Example |
|--------|------|---------|
| `cx` | Shared vCPU (Intel/AMD) | `cx22` (2 vCPU, 4GB) |
| `cax` | Shared vCPU (ARM/Ampere) | `cax11` (2 vCPU, 4GB) |
| `cpx` | Dedicated vCPU (AMD) | `cpx11` (2 vCPU, 2GB) |
| `ccx` | Dedicated high-memory (AMD) | `ccx13` (2 vCPU, 8GB) |
