"""Configuration profiles for FTL2.

Provides functionality to save and reuse common execution configurations.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default profile directory
DEFAULT_PROFILE_DIR = Path.home() / ".ftl2" / "profiles"


@dataclass
class ConfigProfile:
    """A saved configuration profile.

    Attributes:
        name: Profile name
        module: Module to execute
        args: Module arguments
        description: Optional description
        parallel: Number of concurrent connections
        timeout: Execution timeout in seconds
        retry: Number of retry attempts
        retry_delay: Delay between retries
        smart_retry: Whether to use smart retry
        circuit_breaker: Circuit breaker threshold
        format: Output format
        allow_destructive: Whether to allow destructive commands
    """

    name: str
    module: str
    args: dict[str, str] = field(default_factory=dict)
    description: str = ""
    parallel: int | None = None
    timeout: int | None = None
    retry: int | None = None
    retry_delay: float | None = None
    smart_retry: bool | None = None
    circuit_breaker: float | None = None
    format: str | None = None
    allow_destructive: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "name": self.name,
            "module": self.module,
        }
        if self.args:
            result["args"] = self.args
        if self.description:
            result["description"] = self.description
        if self.parallel is not None:
            result["parallel"] = self.parallel
        if self.timeout is not None:
            result["timeout"] = self.timeout
        if self.retry is not None:
            result["retry"] = self.retry
        if self.retry_delay is not None:
            result["retry_delay"] = self.retry_delay
        if self.smart_retry is not None:
            result["smart_retry"] = self.smart_retry
        if self.circuit_breaker is not None:
            result["circuit_breaker"] = self.circuit_breaker
        if self.format is not None:
            result["format"] = self.format
        if self.allow_destructive is not None:
            result["allow_destructive"] = self.allow_destructive
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigProfile":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            module=data["module"],
            args=data.get("args", {}),
            description=data.get("description", ""),
            parallel=data.get("parallel"),
            timeout=data.get("timeout"),
            retry=data.get("retry"),
            retry_delay=data.get("retry_delay"),
            smart_retry=data.get("smart_retry"),
            circuit_breaker=data.get("circuit_breaker"),
            format=data.get("format"),
            allow_destructive=data.get("allow_destructive"),
        )

    def format_text(self) -> str:
        """Format profile as human-readable text."""
        lines = [
            f"Profile: {self.name}",
            f"Module: {self.module}",
        ]
        if self.description:
            lines.append(f"Description: {self.description}")
        if self.args:
            args_str = " ".join(f"{k}={v}" for k, v in self.args.items())
            lines.append(f"Arguments: {args_str}")
        if self.parallel is not None:
            lines.append(f"Parallel: {self.parallel}")
        if self.timeout is not None:
            lines.append(f"Timeout: {self.timeout}s")
        if self.retry is not None:
            lines.append(f"Retry: {self.retry}")
        if self.retry_delay is not None:
            lines.append(f"Retry delay: {self.retry_delay}s")
        if self.smart_retry is not None:
            lines.append(f"Smart retry: {self.smart_retry}")
        if self.circuit_breaker is not None:
            lines.append(f"Circuit breaker: {self.circuit_breaker}%")
        if self.format is not None:
            lines.append(f"Format: {self.format}")
        if self.allow_destructive is not None:
            lines.append(f"Allow destructive: {self.allow_destructive}")
        return "\n".join(lines)

    def apply_args_with_vars(self, variables: dict[str, str]) -> dict[str, str]:
        """Apply template variables to arguments.

        Args:
            variables: Dictionary of variable name -> value

        Returns:
            Arguments with variables substituted
        """
        result = {}
        for key, value in self.args.items():
            # Replace {{var_name}} with variable values
            new_value = value
            for var_name, var_value in variables.items():
                pattern = "{{" + var_name + "}}"
                new_value = new_value.replace(pattern, var_value)
            result[key] = new_value
        return result

    def get_template_variables(self) -> list[str]:
        """Get list of template variables used in arguments.

        Returns:
            List of variable names (without {{ }})
        """
        variables = set()
        pattern = re.compile(r"\{\{(\w+)\}\}")
        for value in self.args.values():
            matches = pattern.findall(str(value))
            variables.update(matches)
        return sorted(variables)


def get_profile_path(name: str, profile_dir: Path | None = None) -> Path:
    """Get the path to a profile file.

    Args:
        name: Profile name
        profile_dir: Optional custom profile directory

    Returns:
        Path to the profile file
    """
    base_dir = profile_dir or DEFAULT_PROFILE_DIR
    # Sanitize name for use in filename
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return base_dir / f"{safe_name}.json"


def load_profile(name: str, profile_dir: Path | None = None) -> ConfigProfile | None:
    """Load a profile from disk.

    Args:
        name: Profile name
        profile_dir: Optional custom profile directory

    Returns:
        ConfigProfile if found, None otherwise
    """
    path = get_profile_path(name, profile_dir)

    if not path.exists():
        logger.debug(f"Profile not found: {path}")
        return None

    try:
        with path.open() as f:
            data = json.load(f)
        return ConfigProfile.from_dict(data)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to load profile {name}: {e}")
        return None


def save_profile(profile: ConfigProfile, profile_dir: Path | None = None) -> Path:
    """Save a profile to disk.

    Args:
        profile: Profile to save
        profile_dir: Optional custom profile directory

    Returns:
        Path where profile was saved
    """
    path = get_profile_path(profile.name, profile_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w") as f:
        json.dump(profile.to_dict(), f, indent=2)

    logger.info(f"Profile saved to {path}")
    return path


def list_profiles(profile_dir: Path | None = None) -> list[str]:
    """List all profile names.

    Args:
        profile_dir: Optional custom profile directory

    Returns:
        List of profile names
    """
    base_dir = profile_dir or DEFAULT_PROFILE_DIR

    if not base_dir.exists():
        return []

    profiles = []
    for path in base_dir.glob("*.json"):
        profiles.append(path.stem)

    return sorted(profiles)


def delete_profile(name: str, profile_dir: Path | None = None) -> bool:
    """Delete a profile.

    Args:
        name: Profile name
        profile_dir: Optional custom profile directory

    Returns:
        True if deleted, False if not found
    """
    path = get_profile_path(name, profile_dir)

    if not path.exists():
        return False

    path.unlink()
    logger.info(f"Profile deleted: {name}")
    return True
