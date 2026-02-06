"""Host filtering and limiting for FTL2.

Provides functionality to filter hosts by patterns, supporting:
- Exact hostnames: web01,web02
- Glob patterns: web*
- Exclusion patterns: !db*
- Group names: @webservers
"""

import fnmatch
import re
from typing import Set


def parse_limit_pattern(pattern: str) -> tuple[Set[str], Set[str], Set[str], Set[str]]:
    """Parse a limit pattern into include/exclude sets.

    Args:
        pattern: Comma-separated list of patterns. Supports:
            - Exact names: web01
            - Glob patterns: web*
            - Exclusions: !db* (prefix with !)
            - Group names: @webservers (prefix with @)

    Returns:
        Tuple of (include_exact, include_patterns, exclude_patterns, include_groups)
    """
    include_exact: Set[str] = set()
    include_patterns: Set[str] = set()
    exclude_patterns: Set[str] = set()
    include_groups: Set[str] = set()

    if not pattern:
        return include_exact, include_patterns, exclude_patterns, include_groups

    for part in pattern.split(","):
        part = part.strip()
        if not part:
            continue

        if part.startswith("!"):
            # Exclusion pattern
            exclude_patterns.add(part[1:])
        elif part.startswith("@"):
            # Group name
            include_groups.add(part[1:])
        elif "*" in part or "?" in part or "[" in part:
            # Glob pattern
            include_patterns.add(part)
        else:
            # Exact hostname
            include_exact.add(part)

    return include_exact, include_patterns, exclude_patterns, include_groups


def match_host(
    hostname: str,
    include_exact: Set[str],
    include_patterns: Set[str],
    exclude_patterns: Set[str],
) -> bool:
    """Check if a hostname matches the filter criteria.

    Args:
        hostname: The hostname to check
        include_exact: Set of exact hostnames to include
        include_patterns: Set of glob patterns to include
        exclude_patterns: Set of glob patterns to exclude

    Returns:
        True if the host should be included, False otherwise
    """
    # Check exclusions first - if excluded, never include
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(hostname, pattern):
            return False

    # If no include criteria, include all (that weren't excluded)
    if not include_exact and not include_patterns:
        return True

    # Check exact match
    if hostname in include_exact:
        return True

    # Check pattern match
    for pattern in include_patterns:
        if fnmatch.fnmatch(hostname, pattern):
            return True

    return False


def filter_hosts(
    all_hosts: dict[str, any],
    limit_pattern: str,
    group_hosts: dict[str, Set[str]] | None = None,
) -> dict[str, any]:
    """Filter hosts based on a limit pattern.

    Args:
        all_hosts: Dictionary of hostname -> host object
        limit_pattern: Comma-separated limit pattern
        group_hosts: Optional mapping of group name -> set of hostnames

    Returns:
        Filtered dictionary of hostname -> host object

    Examples:
        # Include specific hosts
        filter_hosts(hosts, "web01,web02")

        # Include all web servers
        filter_hosts(hosts, "web*")

        # Exclude database servers
        filter_hosts(hosts, "!db*")

        # Combine patterns
        filter_hosts(hosts, "web*,!web03")

        # Include by group
        filter_hosts(hosts, "@webservers", group_hosts)
    """
    if not limit_pattern:
        return all_hosts

    include_exact, include_patterns, exclude_patterns, include_groups = parse_limit_pattern(
        limit_pattern
    )

    # Expand groups to exact hosts
    if include_groups and group_hosts:
        for group_name in include_groups:
            if group_name in group_hosts:
                include_exact.update(group_hosts[group_name])

    # Filter hosts
    result = {}
    for hostname, host in all_hosts.items():
        if match_host(hostname, include_exact, include_patterns, exclude_patterns):
            result[hostname] = host

    return result


def get_group_hosts_mapping(inventory) -> dict[str, Set[str]]:
    """Build a mapping of group names to hostnames from an inventory.

    Args:
        inventory: Inventory object with list_groups() method

    Returns:
        Dictionary mapping group name -> set of hostnames
    """
    group_hosts: dict[str, Set[str]] = {}

    for group in inventory.list_groups():
        group_hosts[group.name] = set(group.hosts.keys())

    return group_hosts


def format_filter_summary(
    original_count: int,
    filtered_count: int,
    limit_pattern: str,
) -> str:
    """Format a summary of host filtering.

    Args:
        original_count: Original number of hosts
        filtered_count: Number of hosts after filtering
        limit_pattern: The limit pattern that was applied

    Returns:
        Human-readable summary string
    """
    if filtered_count == original_count:
        return f"All {original_count} host(s) matched filter: {limit_pattern}"

    excluded = original_count - filtered_count
    return (
        f"Filter '{limit_pattern}': {filtered_count}/{original_count} hosts "
        f"({excluded} excluded)"
    )
