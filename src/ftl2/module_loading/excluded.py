"""Registry of Ansible modules excluded from FTL2.

Some Ansible modules exist only as interfaces to Ansible's internal
execution model (connection plugins, playbook flow control, fact system).
These modules don't apply to FTL2's direct execution model.

When users try to call these modules, FTL2 raises ExcludedModuleError
with helpful guidance on native alternatives.
"""

from dataclasses import dataclass


@dataclass
class ExcludedModule:
    """Metadata about an excluded Ansible module."""

    name: str
    reason: str
    alternative: str
    example: str | None = None


EXCLUDED_MODULES: dict[str, ExcludedModule] = {
    # Connection/Wait modules
    # NOTE: wait_for_connection and ping are now SHADOWED (see shadowed.py)
    # They transparently redirect to native FTL2 methods.
    # NOTE: wait_for is now a real FTL module (ftl_modules/wait_for.py), no longer excluded.
    # Playbook control modules
    "ansible.builtin.meta": ExcludedModule(
        name="meta",
        reason="Controls Ansible playbook execution flow (flush_handlers, end_play, etc.)",
        alternative="Python control flow (return, break, sys.exit())",
    ),
    "ansible.builtin.include_tasks": ExcludedModule(
        name="include_tasks",
        reason="Ansible playbook structure",
        alternative="Python imports and function calls",
        example="""
# Instead of:
await ftl.include_tasks(file="setup.yml")

# Use:
await setup_tasks(ftl)  # Call a Python function
""",
    ),
    "ansible.builtin.import_tasks": ExcludedModule(
        name="import_tasks",
        reason="Ansible playbook structure",
        alternative="Python imports and function calls",
    ),
    "ansible.builtin.include_role": ExcludedModule(
        name="include_role",
        reason="Ansible role system",
        alternative="Python imports and function calls",
    ),
    "ansible.builtin.import_role": ExcludedModule(
        name="import_role",
        reason="Ansible role system",
        alternative="Python imports and function calls",
    ),
    "ansible.builtin.include_vars": ExcludedModule(
        name="include_vars",
        reason="Ansible variable loading",
        alternative="Python: yaml.safe_load() or json.load()",
        example="""
# Instead of:
await ftl.include_vars(file="vars.yml")

# Use:
import yaml
with open("vars.yml") as f:
    vars = yaml.safe_load(f)
""",
    ),
    # Variable/fact modules
    "ansible.builtin.set_fact": ExcludedModule(
        name="set_fact",
        reason="Ansible host fact system",
        alternative="Python variables",
        example="""
# Instead of:
await ftl.minecraft.set_fact(my_var="value")

# Use:
my_var = "value"
""",
    ),
    "ansible.builtin.set_stats": ExcludedModule(
        name="set_stats",
        reason="Ansible playbook statistics",
        alternative="Python variables or logging",
    ),
    "ansible.builtin.group_by": ExcludedModule(
        name="group_by",
        reason="Ansible dynamic group creation from facts",
        alternative="ftl.add_host(..., groups=[...])",
    ),
    # Debugging modules
    "ansible.builtin.debug": ExcludedModule(
        name="debug",
        reason="Ansible debug output",
        alternative="print() or Python logging",
        example="""
# Instead of:
await ftl.minecraft.debug(msg="Hello world")

# Use:
print("Hello world")
""",
    ),
    "ansible.builtin.fail": ExcludedModule(
        name="fail",
        reason="Ansible task failure",
        alternative="raise Exception()",
        example="""
# Instead of:
await ftl.minecraft.fail(msg="Something went wrong")

# Use:
raise RuntimeError("Something went wrong")
""",
    ),
    "ansible.builtin.assert": ExcludedModule(
        name="assert",
        reason="Ansible assertion",
        alternative="Python assert statement",
        example="""
# Instead of:
await ftl.assert(that=["my_var == 'expected'"])

# Use:
assert my_var == "expected", "my_var should be 'expected'"
""",
    ),
    "ansible.builtin.pause": ExcludedModule(
        name="pause",
        reason="Ansible playbook pause",
        alternative="await asyncio.sleep() or input()",
        example="""
# Instead of:
await ftl.minecraft.pause(seconds=30)

# Use:
import asyncio
await asyncio.sleep(30)

# Or for interactive pause:
input("Press Enter to continue...")
""",
    ),
}


def _add_short_names() -> None:
    """Add short name aliases for ansible.builtin modules."""
    additions = {}
    for fqcn, module in EXCLUDED_MODULES.items():
        if fqcn.startswith("ansible.builtin."):
            short = fqcn.replace("ansible.builtin.", "")
            additions[short] = module
    EXCLUDED_MODULES.update(additions)


_add_short_names()


def is_excluded(module_name: str) -> bool:
    """Check if a module is excluded.

    Args:
        module_name: Module name (short name or FQCN)

    Returns:
        True if the module is excluded
    """
    return module_name in EXCLUDED_MODULES


def get_excluded(module_name: str) -> ExcludedModule | None:
    """Get excluded module info if the module is excluded.

    Args:
        module_name: Module name (short name or FQCN)

    Returns:
        ExcludedModule if excluded, None otherwise
    """
    return EXCLUDED_MODULES.get(module_name)
