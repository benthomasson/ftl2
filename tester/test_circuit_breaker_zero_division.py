"""Tests for check_circuit_breaker zero-division fix (Issue #25)."""

import pytest
from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig


@pytest.fixture
def enabled_config():
    return CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=5)


@pytest.fixture
def zero_min_hosts_config():
    return CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=0)


class TestCircuitBreakerZeroDivision:
    """Verify total_hosts=0 does not raise ZeroDivisionError."""

    def test_zero_total_hosts_zero_failed(self, enabled_config):
        """Core bug: total_hosts=0, failed_hosts=0 must not raise."""
        assert check_circuit_breaker(0, 0, enabled_config) is False

    def test_zero_total_hosts_with_min_hosts_zero(self, zero_min_hosts_config):
        """When min_hosts=0, the min_hosts guard passes — zero guard must catch it."""
        assert check_circuit_breaker(0, 0, zero_min_hosts_config) is False

    def test_zero_total_hosts_nonzero_failed(self, enabled_config):
        """Degenerate case: more failures than hosts. Should not raise."""
        assert check_circuit_breaker(0, 5, enabled_config) is False

    def test_one_host_no_failure(self, zero_min_hosts_config):
        """Boundary: total_hosts=1, no failure."""
        assert check_circuit_breaker(1, 0, zero_min_hosts_config) is False

    def test_one_host_one_failure(self, zero_min_hosts_config):
        """Boundary: total_hosts=1, 100% failure, above threshold."""
        assert check_circuit_breaker(1, 1, zero_min_hosts_config) is True

    def test_disabled_zero_hosts(self):
        """Disabled breaker with zero hosts should return False."""
        config = CircuitBreakerConfig(enabled=False)
        assert check_circuit_breaker(0, 0, config) is False

    def test_normal_below_threshold(self, enabled_config):
        """Sanity: normal operation below threshold still works."""
        assert check_circuit_breaker(10, 2, enabled_config) is False

    def test_normal_above_threshold(self, enabled_config):
        """Sanity: normal operation above threshold still works."""
        assert check_circuit_breaker(10, 4, enabled_config) is True
