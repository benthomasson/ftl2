"""Tests for circuit_breaker division by zero fix (issue #25)."""

import os
import pytest


from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig


class TestCircuitBreakerZeroDivision:
    """Test that check_circuit_breaker handles total_hosts=0 without ZeroDivisionError."""

    def test_zero_hosts_zero_failed_min_hosts_zero(self):
        """Core bug: total_hosts=0, failed_hosts=0, min_hosts=0 should not raise."""
        config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=0)
        result = check_circuit_breaker(0, 0, config)
        assert result is False

    def test_zero_hosts_zero_failed_default_min_hosts(self):
        """total_hosts=0 with default min_hosts=5."""
        config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=5)
        assert check_circuit_breaker(0, 0, config) is False

    def test_zero_hosts_nonzero_failed(self):
        """Degenerate case: more failures than total hosts, total=0."""
        config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=0)
        assert check_circuit_breaker(0, 5, config) is False

    def test_zero_hosts_disabled(self):
        """Disabled breaker with zero hosts."""
        config = CircuitBreakerConfig(enabled=False)
        assert check_circuit_breaker(0, 0, config) is False

    def test_normal_below_threshold(self):
        """Sanity: normal operation below threshold still works."""
        config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=5)
        assert check_circuit_breaker(10, 2, config) is False  # 20%

    def test_normal_above_threshold(self):
        """Sanity: normal operation above threshold still triggers."""
        config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=5)
        assert check_circuit_breaker(10, 4, config) is True  # 40%

    def test_exactly_at_threshold(self):
        """Edge: exactly at threshold should trigger."""
        config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=5)
        assert check_circuit_breaker(10, 3, config) is True  # 30%

    def test_one_host_zero_failures(self):
        """Edge: single host, no failures."""
        config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=0)
        assert check_circuit_breaker(1, 0, config) is False

    def test_one_host_one_failure(self):
        """Edge: single host, one failure (100%) should trigger."""
        config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=0)
        assert check_circuit_breaker(1, 1, config) is True
