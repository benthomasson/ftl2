# FTL2 Roadmap

## Near-term

### Terraform Provider Integration
Call Terraform providers as an alternative to Ansible modules. Access 3000+ providers not covered by Ansible collections. Requires: provider binary discovery, gRPC protocol bridge (Python to Go), state mapping to `.ftl2-state.json`, plan/apply semantics.

### Multi-Cloud Validation
Port Linode-based scripts (minecraft provisioning, image builder, scale tests) to AWS using `amazon.aws.*` modules. Proves the automation context API works across clouds with the same patterns.

### Module Discovery API
`ftl.list_modules(category=None)` for AI to discover available modules at runtime. `ftl.describe(module_name)` for detailed parameter info. Needed for AI-generated scripts to select the right modules.

### Policy Engine Improvements
Allow rule short-circuiting (not just deny). Policy validation on load (warn about unknown match keys). Policy composition (multiple files, directory of policies). Surface policy summary in audit log.

## Medium-term

### Declarative Resource Planning
Terraform-style resource declarations with automatic dependency inference:

```python
plan = ftl.resources()
vpc = plan.vpc("main", cidr="10.0.0.0/16")
subnet = plan.subnet(vpc_id=vpc["vpc_id"])  # dependency inferred
await plan.apply()   # topological sort, parallel execution waves
await plan.destroy()  # reverse order teardown
```

### Ansible-to-FTL2 Converter
Bidirectional conversion: Ansible playbooks to FTL2 Python scripts and back. Maps: hosts to group proxies, vars to Python variables, with_items to for loops, when to if statements, handlers to explicit service restarts, roles to function calls.

### Transaction/Rollback
Atomic operations across multiple modules. Explicit rollback on failure. Savepoint/restore patterns for complex multi-step workflows.

## Longer-term

### Event-Driven Workflows
Gate process emits events back to controller for reactive automation. File watch (redeploy on config change), process monitoring (restart on crash), log tailing (alert on pattern match), system thresholds (scale on resource pressure).

### Desired State as Markdown
Markdown documents express infrastructure requirements in natural English. AI generates scripts from markdown (disposable, regenerate on changes). State file is ground truth. Drift detection: compare markdown spec against actual state, report differences.
