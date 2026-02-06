"""Module documentation and discovery system for FTL2.

Provides functionality to list available modules, extract documentation
from module docstrings, and generate usage examples.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ModuleArgument:
    """Documentation for a module argument.

    Attributes:
        name: Argument name
        type: Argument type (str, int, bool, etc.)
        required: Whether the argument is required
        description: Description of the argument
        default: Default value if optional
        choices: Valid choices for enum-type arguments
    """

    name: str
    type: str = "str"
    required: bool = False
    description: str = ""
    default: str | None = None
    choices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }
        if self.default is not None:
            result["default"] = self.default
        if self.choices:
            result["choices"] = self.choices
        return result


@dataclass
class ModuleReturn:
    """Documentation for a module return value.

    Attributes:
        name: Return field name
        type: Return field type
        description: Description of the return field
    """

    name: str
    type: str = "str"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
        }


@dataclass
class BackupMetadata:
    """Backup capability metadata for a module.

    Attributes:
        capable: Whether the module supports automatic backups
        paths: List of argument names that contain paths to back up
        triggers: List of operations that trigger backup (modify, delete)
    """

    capable: bool = False
    paths: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=lambda: ["modify", "delete"])

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "capable": self.capable,
            "paths": self.paths,
            "triggers": self.triggers,
        }

    @classmethod
    def from_parsed(
        cls,
        capable: bool | None,
        paths_str: str | None,
        triggers_str: str | None,
    ) -> "BackupMetadata":
        """Create from parsed docstring values."""
        paths = []
        if paths_str:
            paths = [p.strip() for p in paths_str.split(",") if p.strip()]

        triggers = ["modify", "delete"]  # defaults
        if triggers_str:
            triggers = [t.strip().lower() for t in triggers_str.split(",") if t.strip()]

        return cls(
            capable=capable if capable is not None else False,
            paths=paths,
            triggers=triggers,
        )


@dataclass
class ModuleDoc:
    """Documentation for a module.

    Attributes:
        name: Module name (without .py extension)
        path: Path to the module file
        short_description: One-line description
        long_description: Full description
        arguments: List of module arguments
        returns: List of return values
        examples: Usage examples
        idempotent: Whether the module is idempotent
        backup: Backup capability metadata
    """

    name: str
    path: Path
    short_description: str = ""
    long_description: str = ""
    arguments: list[ModuleArgument] = field(default_factory=list)
    returns: list[ModuleReturn] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    idempotent: bool | None = None
    backup: BackupMetadata = field(default_factory=BackupMetadata)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "name": self.name,
            "path": str(self.path),
            "short_description": self.short_description,
        }
        if self.long_description:
            result["long_description"] = self.long_description
        if self.arguments:
            result["arguments"] = [arg.to_dict() for arg in self.arguments]
        if self.returns:
            result["returns"] = [ret.to_dict() for ret in self.returns]
        if self.examples:
            result["examples"] = self.examples
        if self.idempotent is not None:
            result["idempotent"] = self.idempotent
        if self.backup.capable:
            result["backup"] = self.backup.to_dict()
        return result

    def format_text(self) -> str:
        """Format module documentation as human-readable text."""
        lines = [
            f"Module: {self.name}",
            f"Description: {self.short_description}",
        ]

        if self.long_description:
            lines.append("")
            lines.append(self.long_description)

        if self.arguments:
            lines.append("")
            lines.append("Arguments:")
            for arg in self.arguments:
                req = "required" if arg.required else "optional"
                default_str = f", default: {arg.default}" if arg.default else ""
                lines.append(f"  {arg.name} ({arg.type}, {req}{default_str})")
                if arg.description:
                    lines.append(f"    {arg.description}")
                if arg.choices:
                    lines.append(f"    Choices: {', '.join(arg.choices)}")

        if self.returns:
            lines.append("")
            lines.append("Returns:")
            for ret in self.returns:
                lines.append(f"  {ret.name} ({ret.type}): {ret.description}")

        if self.examples:
            lines.append("")
            lines.append("Examples:")
            for example in self.examples:
                lines.append(f"  {example}")

        if self.idempotent is not None:
            lines.append("")
            lines.append(f"Idempotent: {'Yes' if self.idempotent else 'No'}")

        if self.backup.capable:
            lines.append("")
            lines.append(f"Backup-Capable: Yes")
            if self.backup.paths:
                lines.append(f"Backup-Paths: {', '.join(self.backup.paths)}")
            if self.backup.triggers:
                lines.append(f"Backup-Trigger: {', '.join(self.backup.triggers)}")

        lines.append("")
        lines.append(f"Path: {self.path}")

        return "\n".join(lines)


def parse_module_docstring(docstring: str) -> dict[str, Any]:
    """Parse a module docstring to extract documentation.

    Args:
        docstring: The module's docstring

    Returns:
        Dictionary with parsed documentation fields
    """
    if not docstring:
        return {}

    lines = docstring.strip().split("\n")
    result: dict[str, Any] = {
        "short_description": "",
        "long_description": "",
        "arguments": [],
        "returns": [],
        "idempotent": None,  # Will be True, False, or None if not specified
        "backup_capable": None,
        "backup_paths": None,
        "backup_trigger": None,
    }

    # First line is typically "Module name - Short description"
    if lines:
        first_line = lines[0].strip()
        if " - " in first_line:
            _, _, desc = first_line.partition(" - ")
            result["short_description"] = desc.strip()
        else:
            result["short_description"] = first_line

    # Parse sections
    current_section = "description"
    current_text: list[str] = []
    current_arg: dict[str, Any] | None = None

    for line in lines[1:]:
        stripped = line.strip()

        # Check for section headers
        if stripped.startswith("Arguments:"):
            # Save any accumulated description
            if current_text and current_section == "description":
                result["long_description"] = " ".join(current_text).strip()
            current_section = "arguments"
            current_text = []
            current_arg = None
            continue
        elif stripped.startswith("Returns:"):
            current_section = "returns"
            current_text = []
            current_arg = None
            continue
        elif stripped.startswith("Examples:"):
            current_section = "examples"
            current_text = []
            continue

        # Check for Idempotent: Yes/No (can appear at any point)
        idempotent_match = re.match(r"Idempotent:\s*(Yes|No|True|False)", stripped, re.IGNORECASE)
        if idempotent_match:
            value = idempotent_match.group(1).lower()
            result["idempotent"] = value in ("yes", "true")
            continue

        # Check for Backup-Capable: Yes/No
        backup_capable_match = re.match(r"Backup-Capable:\s*(Yes|No|True|False)", stripped, re.IGNORECASE)
        if backup_capable_match:
            value = backup_capable_match.group(1).lower()
            result["backup_capable"] = value in ("yes", "true")
            continue

        # Check for Backup-Paths: path,dest
        backup_paths_match = re.match(r"Backup-Paths:\s*(.+)", stripped, re.IGNORECASE)
        if backup_paths_match:
            result["backup_paths"] = backup_paths_match.group(1).strip()
            continue

        # Check for Backup-Trigger: modify,delete
        backup_trigger_match = re.match(r"Backup-Trigger:\s*(.+)", stripped, re.IGNORECASE)
        if backup_trigger_match:
            result["backup_trigger"] = backup_trigger_match.group(1).strip()
            continue

        # Process content based on section
        if current_section == "description":
            if stripped:
                current_text.append(stripped)

        elif current_section == "arguments":
            # Argument format: "name (type, required/optional): description"
            # or continuation lines starting with spaces or "-"
            arg_match = re.match(r"(\w+)\s*\(([^)]+)\):\s*(.*)", stripped)
            if arg_match:
                name, type_info, desc = arg_match.groups()

                # Parse type and required status
                type_parts = [p.strip() for p in type_info.split(",")]
                arg_type = type_parts[0] if type_parts else "str"
                required = "required" in type_info.lower()

                # Check for default value
                default = None
                default_match = re.search(r"[Dd]efault:\s*[\"']?([^\"']+)[\"']?", desc)
                if default_match:
                    default = default_match.group(1).strip()

                current_arg = {
                    "name": name,
                    "type": arg_type,
                    "required": required,
                    "description": desc,
                    "default": default,
                    "choices": [],
                }
                result["arguments"].append(current_arg)

            elif stripped.startswith("-") and current_arg:
                # This is a choice/option for the previous argument
                choice = stripped.lstrip("- ").split(":")[0].strip()
                current_arg["choices"].append(choice)

        elif current_section == "returns":
            # Return format: "name (type): description"
            ret_match = re.match(r"(\w+)\s*\(([^)]+)\):\s*(.*)", stripped)
            if ret_match:
                name, ret_type, desc = ret_match.groups()
                result["returns"].append({
                    "name": name,
                    "type": ret_type,
                    "description": desc,
                })

    # Save any remaining description
    if current_text and current_section == "description":
        result["long_description"] = " ".join(current_text).strip()

    return result


def extract_module_doc(module_path: Path) -> ModuleDoc:
    """Extract documentation from a module file.

    Args:
        module_path: Path to the module file

    Returns:
        ModuleDoc with extracted documentation
    """
    name = module_path.stem

    # Read and parse the module file
    content = module_path.read_text()

    # Extract docstring (first triple-quoted string)
    docstring = ""
    docstring_match = re.search(r'^"""(.*?)"""', content, re.MULTILINE | re.DOTALL)
    if not docstring_match:
        docstring_match = re.search(r"^'''(.*?)'''", content, re.MULTILINE | re.DOTALL)
    if docstring_match:
        docstring = docstring_match.group(1)

    # Parse the docstring
    parsed = parse_module_docstring(docstring)

    # Build ModuleDoc
    arguments = [
        ModuleArgument(
            name=arg["name"],
            type=arg.get("type", "str"),
            required=arg.get("required", False),
            description=arg.get("description", ""),
            default=arg.get("default"),
            choices=arg.get("choices", []),
        )
        for arg in parsed.get("arguments", [])
    ]

    returns = [
        ModuleReturn(
            name=ret["name"],
            type=ret.get("type", "str"),
            description=ret.get("description", ""),
        )
        for ret in parsed.get("returns", [])
    ]

    # Generate examples based on module type
    examples = generate_examples(name, arguments)

    # Determine idempotency - prefer parsed value from docstring
    idempotent = parsed.get("idempotent")
    if idempotent is None:
        # Fall back to hardcoded lists for modules without declaration
        idempotent_modules = {"ping", "setup", "file", "copy"}
        non_idempotent_modules = {"shell", "command", "script"}
        if name in idempotent_modules:
            idempotent = True
        elif name in non_idempotent_modules:
            idempotent = False

    # Build backup metadata
    backup = BackupMetadata.from_parsed(
        capable=parsed.get("backup_capable"),
        paths_str=parsed.get("backup_paths"),
        triggers_str=parsed.get("backup_trigger"),
    )

    return ModuleDoc(
        name=name,
        path=module_path,
        short_description=parsed.get("short_description", ""),
        long_description=parsed.get("long_description", ""),
        arguments=arguments,
        returns=returns,
        examples=examples,
        idempotent=idempotent,
        backup=backup,
    )


def generate_examples(module_name: str, arguments: list[ModuleArgument]) -> list[str]:
    """Generate usage examples for a module.

    Args:
        module_name: Name of the module
        arguments: Module arguments

    Returns:
        List of example command strings
    """
    examples = []

    # Basic example
    examples.append(f"ftl2 run -m {module_name} -i hosts.yml")

    # Module-specific examples
    if module_name == "ping":
        examples.append(f'ftl2 run -m {module_name} -i hosts.yml -a "data=hello"')

    elif module_name == "file":
        examples.append(f'ftl2 run -m {module_name} -i hosts.yml -a "path=/tmp/test state=touch"')
        examples.append(f'ftl2 run -m {module_name} -i hosts.yml -a "path=/opt/app state=directory mode=0755"')
        examples.append(f'ftl2 run -m {module_name} -i hosts.yml -a "path=/tmp/old state=absent"')

    elif module_name == "shell":
        examples.append(f'ftl2 run -m {module_name} -i hosts.yml -a "cmd=uptime"')
        examples.append(f"ftl2 run -m {module_name} -i hosts.yml -a \"cmd='df -h'\"")

    elif module_name == "copy":
        examples.append(f'ftl2 run -m {module_name} -i hosts.yml -a "src=./app.tar.gz dest=/opt/"')

    elif module_name == "setup":
        examples.append(f"ftl2 run -m {module_name} -i hosts.yml --format json")

    return examples


def discover_modules(module_dirs: list[Path]) -> list[ModuleDoc]:
    """Discover all available modules in the given directories.

    Args:
        module_dirs: List of directories to search for modules

    Returns:
        List of ModuleDoc for each discovered module
    """
    modules: dict[str, ModuleDoc] = {}  # Use dict to handle duplicates

    for module_dir in module_dirs:
        if not module_dir.exists():
            continue

        for module_path in module_dir.glob("*.py"):
            # Skip __init__.py and other special files
            if module_path.name.startswith("_"):
                continue

            name = module_path.stem

            # First occurrence wins (user modules override built-ins)
            if name not in modules:
                try:
                    doc = extract_module_doc(module_path)
                    modules[name] = doc
                except Exception:
                    # If we can't parse the module, create minimal doc
                    modules[name] = ModuleDoc(
                        name=name,
                        path=module_path,
                        short_description="(no documentation available)",
                    )

    # Sort by name
    return sorted(modules.values(), key=lambda m: m.name)


def format_module_list(modules: list[ModuleDoc]) -> str:
    """Format a list of modules for display.

    Args:
        modules: List of module documentation

    Returns:
        Formatted string for display
    """
    if not modules:
        return "No modules found."

    lines = ["", "Available modules:", ""]

    # Find max name length for alignment
    max_name = max(len(m.name) for m in modules)

    for module in modules:
        padding = " " * (max_name - len(module.name) + 2)
        desc = module.short_description or "(no description)"
        lines.append(f"  {module.name}{padding}{desc}")

    lines.append("")
    lines.append(f"Total: {len(modules)} module(s)")
    lines.append("")
    lines.append("Use 'ftl2 module doc <name>' for detailed documentation.")
    lines.append("")

    return "\n".join(lines)


def format_module_list_json(modules: list[ModuleDoc]) -> list[dict[str, Any]]:
    """Format a list of modules as JSON-serializable list.

    Args:
        modules: List of module documentation

    Returns:
        List of dictionaries for JSON serialization
    """
    return [
        {
            "name": m.name,
            "path": str(m.path),
            "description": m.short_description,
        }
        for m in modules
    ]
