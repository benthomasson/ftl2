"""Tests for circuit_breaker division by zero fix (issue #25).

Validates that check_circuit_breaker handles total_hosts=0 without
raising ZeroDivisionError, and that the fix doesn't break existing behavior.
"""

import os

from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig


def make_config(enabled=True, threshold=30.0, min_hosts=5):
    return CircuitBreakerConfig(enabled=enabled, threshold_percent=threshold, min_hosts=min_hosts)


# --- Core bug fix tests ---

def test_zero_hosts_zero_failed_no_exception():
    """The original bug: total_hosts=0, failed_hosts=0 should not raise."""
    result = check_circuit_breaker(0, 0, make_config())
    assert result is False


def test_zero_hosts_with_min_hosts_zero():
    """Zero hosts with min_hosts=0 (wouldn't be caught by min_hosts guard)."""
    result = check_circuit_breaker(0, 0, make_config(min_hosts=0))
    assert result is False


def test_zero_hosts_disabled():
    """Zero hosts with disabled config — early return before division."""
    result = check_circuit_breaker(0, 0, make_config(enabled=False))
    assert result is False


# --- Edge cases around the fix ---

def test_one_host_no_failure():
    """Single host, no failure — should not trigger."""
    assert not check_circuit_breaker(1, 0, make_config(min_hosts=0))


def test_one_host_one_failure():
    """Single host, 100% failure, min_hosts=0 — should trigger."""
    assert check_circuit_breaker(1, 1, make_config(min_hosts=0, threshold=50.0))


def test_below_threshold():
    """Failures below threshold — should not trigger."""
    assert not check_circuit_breaker(10, 2, make_config(min_hosts=5, threshold=30.0))


def test_above_threshold():
    """Failures above threshold — should trigger."""
    assert check_circuit_breaker(10, 4, make_config(min_hosts=5, threshold=30.0))


def test_at_exact_threshold():
    """Failures exactly at threshold — should trigger (>= check)."""
    # 3/10 = 30%, threshold=30% → should trigger
    assert check_circuit_breaker(10, 3, make_config(min_hosts=5, threshold=30.0))


def test_below_min_hosts():
    """Even 100% failure with too few hosts — should not trigger."""
    assert not check_circuit_breaker(3, 3, make_config(min_hosts=5))


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
