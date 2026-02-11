"""Policy engine for FTL2.

Evaluates YAML-based rules against proposed actions before execution.
In an AI-first model, policy (should this action be permitted given the
context) replaces RBAC (who can do what) since the actor is always the
AI loop or a script.
"""

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PolicyDeniedError(Exception):
    """Raised when a policy rule denies an action."""

    def __init__(self, message: str, rule: "PolicyRule | None" = None):
        super().__init__(message)
        self.rule = rule


@dataclass
class PolicyRule:
    """A single policy rule.

    Attributes:
        decision: "allow" or "deny"
        match: Conditions to match against. Keys can be:
            - module: fnmatch pattern against module name
            - host: fnmatch pattern against target host
            - environment: match against environment label
            - param.<name>: match against a specific parameter value
        reason: Human-readable explanation for the rule
    """

    decision: str
    match: dict[str, str] = field(default_factory=dict)
    reason: str = ""


@dataclass
class PolicyResult:
    """Result of a policy evaluation.

    Attributes:
        permitted: Whether the action is allowed
        rule: The rule that matched, or None (default allow)
        reason: Human-readable explanation
    """

    permitted: bool
    rule: PolicyRule | None = None
    reason: str = ""


class Policy:
    """Policy engine that evaluates rules against proposed actions.

    Rules are evaluated top-to-bottom. The first matching deny rule
    causes the action to be denied. If no deny rule matches, the
    action is permitted.
    """

    def __init__(self, rules: list[PolicyRule]):
        self.rules = rules

    def evaluate(
        self,
        module_name: str,
        params: dict[str, Any],
        host: str = "localhost",
        environment: str = "",
    ) -> PolicyResult:
        """Evaluate all rules against a proposed action.

        Args:
            module_name: Name of the module to execute
            params: Module parameters
            host: Target host name
            environment: Environment label

        Returns:
            PolicyResult indicating whether the action is permitted
        """
        for rule in self.rules:
            if rule.decision != "deny":
                continue

            if self._matches(rule, module_name, params, host, environment):
                logger.debug(
                    "Policy denied %s on %s: %s", module_name, host, rule.reason
                )
                return PolicyResult(
                    permitted=False, rule=rule, reason=rule.reason
                )

        logger.debug("Policy permitted %s on %s", module_name, host)
        return PolicyResult(permitted=True, reason="No matching deny rule")

    @staticmethod
    def _matches(
        rule: PolicyRule,
        module_name: str,
        params: dict[str, Any],
        host: str,
        environment: str,
    ) -> bool:
        """Check if a rule matches the given action context.

        All conditions in the rule must match for the rule to apply.
        """
        for key, pattern in rule.match.items():
            pattern = str(pattern)

            if key == "module":
                if not fnmatch.fnmatch(module_name, pattern):
                    return False
            elif key == "host":
                if not fnmatch.fnmatch(host, pattern):
                    return False
            elif key == "environment":
                if not fnmatch.fnmatch(environment, pattern):
                    return False
            elif key.startswith("param."):
                param_name = key[len("param."):]
                param_value = str(params.get(param_name, ""))
                if not fnmatch.fnmatch(param_value, pattern):
                    return False
            else:
                # Unknown condition key — skip (don't match)
                return False

        return True

    @classmethod
    def from_file(cls, path: str | Path) -> "Policy":
        """Load policy from a YAML file.

        Expected format:
            rules:
              - decision: deny
                match:
                  module: "shell"
                  environment: "prod"
                reason: "Use proper modules in production"

        Args:
            path: Path to the YAML policy file

        Returns:
            Policy instance with loaded rules
        """
        import yaml

        path = Path(path)
        data = yaml.safe_load(path.read_text())

        rules = []
        for entry in data.get("rules", []):
            rules.append(
                PolicyRule(
                    decision=entry.get("decision", "deny"),
                    match=entry.get("match", {}),
                    reason=entry.get("reason", ""),
                )
            )

        return cls(rules)

    @classmethod
    def empty(cls) -> "Policy":
        """Return a policy that permits everything."""
        return cls(rules=[])
