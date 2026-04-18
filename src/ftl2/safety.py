"""Safety checks and destructive command detection for FTL2.

Provides functionality to detect potentially dangerous commands and
enforce safe defaults with explicit override requirements.
"""

import re
from dataclasses import dataclass, field
from typing import Any

# Patterns that indicate destructive commands
DESTRUCTIVE_PATTERNS = [
    # rm with force/recursive flags
    (r"\brm\s+(-[rfR]+|--force|--recursive)", "rm with force/recursive flags"),
    (r"\brm\s+.*\s+-[rfR]", "rm with force/recursive flags"),
    # rmdir
    (r"\brmdir\b", "rmdir command"),
    # dd command (can overwrite disks)
    (r"\bdd\s+", "dd command (can overwrite disks)"),
    # mkfs (format filesystem)
    (r"\bmkfs\b", "mkfs command (formats filesystem)"),
    # Truncate files
    (r">\s*/", "redirect overwriting file"),
    (r">\s*~", "redirect overwriting file in home"),
    # Kill all processes
    (r"\bkillall\b", "killall command"),
    (r"\bpkill\s+-9", "pkill with SIGKILL"),
    # System shutdown/reboot
    (r"\b(shutdown|reboot|halt|poweroff)\b", "system shutdown/reboot command"),
    # chmod/chown recursive on system paths
    (r"\bchmod\s+(-R|--recursive)\s+.*\s+/(?!tmp|home)", "recursive chmod on system path"),
    (r"\bchown\s+(-R|--recursive)\s+.*\s+/(?!tmp|home)", "recursive chown on system path"),
    # Database drop commands
    (r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b", "SQL DROP command"),
    # Docker/container remove all
    (r"\bdocker\s+(rm|rmi)\s+.*-f", "docker force remove"),
    (r"\bdocker\s+system\s+prune", "docker system prune"),
    # Git destructive commands
    (r"\bgit\s+(reset\s+--hard|clean\s+-f|push\s+.*--force)", "destructive git command"),
    # iptables flush
    (r"\biptables\s+-F", "iptables flush"),
    # systemctl stop/disable critical services
    (r"\bsystemctl\s+(stop|disable)\s+(sshd|ssh|network)", "stopping critical system service"),
]

# Patterns that are always blocked (too dangerous)
BLOCKED_PATTERNS = [
    (r"\brm\s+-rf\s+/\s*$", "rm -rf / (would destroy entire filesystem)"),
    (r"\brm\s+-rf\s+/\*", "rm -rf /* (would destroy entire filesystem)"),
    (r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}", "fork bomb"),
    (r"\bdd\s+.*of=/dev/[sh]d[a-z]\b", "dd writing to raw disk device"),
]

# Safe path prefixes (destructive operations on these are allowed)
SAFE_PATHS = [
    "/tmp/",
    "/var/tmp/",
    "/dev/shm/",
]


@dataclass
class SafetyCheckResult:
    """Result of a safety check.

    Attributes:
        safe: Whether the command is considered safe
        blocked: Whether the command is completely blocked (cannot override)
        warnings: List of warning messages about potential risks
        blocked_reason: Reason if command is blocked
    """

    safe: bool = True
    blocked: bool = False
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "safe": self.safe,
            "blocked": self.blocked,
            "warnings": self.warnings,
            "blocked_reason": self.blocked_reason,
        }

    def format_text(self) -> str:
        """Format as human-readable text."""
        if self.blocked:
            return f"BLOCKED: {self.blocked_reason}"
        elif not self.safe:
            lines = ["Potentially destructive command detected:"]
            for warning in self.warnings:
                lines.append(f"  - {warning}")
            lines.append("")
            lines.append("Use --allow-destructive to run this command.")
            return "\n".join(lines)
        return "OK"


def _is_safe_path(cmd: str) -> bool:
    """Check if the command only operates on safe paths."""
    return any(safe_path in cmd for safe_path in SAFE_PATHS)


def check_command_safety(cmd: str) -> SafetyCheckResult:
    """Check if a shell command is potentially destructive.

    Args:
        cmd: The shell command to check

    Returns:
        SafetyCheckResult with safety assessment
    """
    result = SafetyCheckResult()

    # Normalize command for pattern matching
    normalized = cmd.strip()

    # Check for blocked patterns first (cannot be overridden)
    for pattern, reason in BLOCKED_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            result.blocked = True
            result.safe = False
            result.blocked_reason = reason
            return result

    # Check for destructive patterns
    for pattern, description in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            # Check if it's operating on safe paths
            if not _is_safe_path(normalized):
                result.safe = False
                result.warnings.append(description)

    return result


def check_module_args_safety(
    module_name: str,
    module_args: dict[str, Any],
) -> SafetyCheckResult:
    """Check if module arguments are potentially destructive.

    Args:
        module_name: Name of the module being executed
        module_args: Arguments being passed to the module

    Returns:
        SafetyCheckResult with safety assessment
    """
    result = SafetyCheckResult()

    # Check shell/command module
    if module_name in ("shell", "command", "script"):
        cmd = module_args.get("cmd", "") or module_args.get("_raw_params", "")
        if cmd:
            cmd_result = check_command_safety(cmd)
            if cmd_result.blocked or not cmd_result.safe:
                return cmd_result

    # Check file module with state=absent
    if module_name == "file":
        state = module_args.get("state", "")
        path = module_args.get("path", "")

        if state == "absent" and path:
            # Check if removing something outside safe paths
            if not any(path.startswith(safe) for safe in SAFE_PATHS):
                # Check for dangerous paths
                if path == "/" or path.startswith("/etc/") or path.startswith("/usr/"):
                    result.safe = False
                    result.warnings.append(f"Removing file/directory: {path}")

    return result


def format_safety_error(result: SafetyCheckResult, module_name: str) -> str:
    """Format a safety check failure as an error message.

    Args:
        result: The safety check result
        module_name: Name of the module

    Returns:
        Formatted error message
    """
    if result.blocked:
        return (
            f"Command blocked for safety: {result.blocked_reason}\n"
            f"This command cannot be executed through FTL2."
        )

    lines = [
        f"Destructive command detected in module '{module_name}':",
        "",
    ]
    for warning in result.warnings:
        lines.append(f"  - {warning}")

    lines.extend([
        "",
        "To execute this command, use one of these options:",
        "  --allow-destructive    Allow this specific execution",
        "",
        "Review the command carefully before proceeding.",
    ])

    return "\n".join(lines)


# Default safety settings
DEFAULT_PARALLEL = 10  # Default concurrent connections
DEFAULT_TIMEOUT = 300  # Default timeout in seconds (5 minutes)
MAX_PARALLEL = 100  # Maximum allowed parallel connections
