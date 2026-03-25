"""Tests for circuit_breaker division by zero fix (Issue #25).

Validates that check_circuit_breaker handles total_hosts=0 without
raising ZeroDivisionError.
"""

import pytest
from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig


@pytest.fixture
def enabled_config():
    return CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=5)


@pytest.fixture
def zero_min_hosts_config():
    return CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=0)


class TestCircuitBreakerZeroDivision:
    """Tests for the zero-hosts guard (Issue #25)."""

    def test_zero_hosts_zero_failed_returns_false(self, enabled_config):
        """Primary bug case: total_hosts=0, failed_hosts=0 should not raise."""
        assert check_circuit_breaker(0, 0, enabled_config) is False

    def test_zero_hosts_with_min_hosts_zero(self, zero_min_hosts_config):
        """Edge case: min_hosts=0 would previously pass the min_hosts check,
        then hit division by zero."""
        assert check_circuit_breaker(0, 0, zero_min_hosts_config) is False

    def test_zero_hosts_nonzero_failed(self, enabled_config):
        """Degenerate case: more failures than hosts (shouldn't happen, but shouldn't crash)."""
        assert check_circuit_breaker(0, 5, enabled_config) is False

    def test_one_host_no_failures(self, enabled_config):
        """Boundary: single host with no failures, below min_hosts."""
        assert check_circuit_breaker(1, 0, enabled_config) is False

    def test_one_host_one_failure_below_min_hosts(self, enabled_config):
        """Single host, 100% failure, but below min_hosts=5."""
        assert check_circuit_breaker(1, 1, enabled_config) is False

    def test_normal_above_threshold_triggers(self, enabled_config):
        """Sanity: normal case still triggers when above threshold."""
        assert check_circuit_breaker(10, 4, enabled_config) is True  # 40% >= 30%

    def test_normal_below_threshold_no_trigger(self, enabled_config):
        """Sanity: normal case below threshold doesn't trigger."""
        assert check_circuit_breaker(10, 2, enabled_config) is False  # 20% < 30%

    def test_disabled_with_zero_hosts(self):
        """Disabled breaker with zero hosts should still return False."""
        config = CircuitBreakerConfig(enabled=False)
        assert check_circuit_breaker(0, 0, config) is False
