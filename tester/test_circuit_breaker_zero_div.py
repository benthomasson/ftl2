"""Tests for check_circuit_breaker zero-division fix (Issue #25)."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig


def make_config(enabled=True, threshold=30.0, min_hosts=5):
    return CircuitBreakerConfig(enabled=enabled, threshold_percent=threshold, min_hosts=min_hosts)


def test_zero_hosts_zero_failed_no_exception():
    """Core bug: total_hosts=0 must not raise ZeroDivisionError."""
    result = check_circuit_breaker(0, 0, make_config())
    assert result is False


def test_zero_hosts_with_failed_hosts():
    """Edge: failed_hosts > 0 but total_hosts = 0 (logically impossible but shouldn't crash)."""
    result = check_circuit_breaker(0, 5, make_config())
    assert result is False


def test_zero_hosts_disabled():
    """Disabled breaker with zero hosts."""
    result = check_circuit_breaker(0, 0, make_config(enabled=False))
    assert result is False


def test_zero_hosts_min_hosts_zero():
    """Zero hosts with min_hosts=0 config — still should not divide."""
    config = make_config(min_hosts=0)
    result = check_circuit_breaker(0, 0, config)
    assert result is False


def test_normal_below_threshold():
    """Sanity: below threshold returns False."""
    assert check_circuit_breaker(10, 2, make_config()) is False  # 20%


def test_normal_above_threshold():
    """Sanity: above threshold returns True."""
    assert check_circuit_breaker(10, 4, make_config()) is True  # 40%


def test_one_host_zero_failures():
    """Single host, no failure — should not trip."""
    config = make_config(min_hosts=1)
    assert check_circuit_breaker(1, 0, config) is False


def test_one_host_one_failure():
    """Single host, 100% failure — should trip if above min_hosts."""
    config = make_config(min_hosts=1, threshold=50.0)
    assert check_circuit_breaker(1, 1, config) is True


def test_exact_threshold():
    """Failure rate exactly at threshold should trigger."""
    config = make_config(threshold=30.0, min_hosts=1)
    assert check_circuit_breaker(10, 3, config) is True  # exactly 30%
