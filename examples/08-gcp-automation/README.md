# 08 - GCP Automation

Provision and configure Google Cloud Platform infrastructure with FTL2.

## Setup

```bash
# Install the google.cloud Ansible collection
ANSIBLE_COLLECTIONS_PATH=.venv/lib/python3.14/site-packages \
  .venv/bin/ansible-galaxy collection install google.cloud

# Install Python dependencies
uv pip install google-auth google-cloud-compute google-api-python-client
```

## Authentication

```bash
# Option 1: Service account key
export GCP_AUTH_KIND=serviceaccount
export GCP_SERVICE_ACCOUNT_FILE=/path/to/service-account.json

# Option 2: Application default credentials
export GCP_AUTH_KIND=application
gcloud auth application-default login
```

## Examples

| Script | What it does |
|--------|-------------|
| `example_gcp_provision.py` | Creates VPC, subnet, firewall rules, GCE instance, installs nginx |
| `example_gcp_teardown.py` | Deletes all resources created by the provision script |

## Available GCP Modules

The `google.cloud` collection provides 60+ modules including:

- `gcp_compute_instance` — VM instances
- `gcp_compute_network` — VPC networks
- `gcp_compute_subnetwork` — Subnets
- `gcp_compute_firewall` — Firewall rules
- `gcp_compute_disk` — Persistent disks
- `gcp_compute_address` — Static IPs
- `gcp_compute_forwarding_rule` — Load balancer forwarding rules
- `gcp_compute_health_check` — Health checks
- `gcp_compute_instance_group_manager` — Managed instance groups
- `gcp_compute_instance_template` — Instance templates
- `gcp_sql_instance` — Cloud SQL instances
- `gcp_sql_database` — Cloud SQL databases
- `gcp_sql_user` — Cloud SQL users
- `gcp_storage_bucket` — Cloud Storage buckets
- `gcp_storage_object` — Cloud Storage objects
- `gcp_dns_managed_zone` — Cloud DNS zones
- `gcp_dns_resource_record_set` — DNS records

All modules use the same FTL2 async pattern:

```python
async with automation(secret_bindings={"google.cloud.*": {...}}) as ftl:
    await ftl.google.cloud.gcp_compute_instance(name="vm", ...)
```
