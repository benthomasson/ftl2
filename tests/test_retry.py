"""Tests for ftl2.retry module."""



from ftl2.exceptions import ErrorTypes
from ftl2.retry import (
    CircuitBreakerConfig,
    RetryConfig,
    RetryState,
    RetryStats,
    check_circuit_breaker,
    format_retry_summary,
    is_permanent_error,
    is_transient_error,
    should_retry,
)


class TestErrorClassification:
    def test_transient_errors(self):
        assert is_transient_error(ErrorTypes.CONNECTION_TIMEOUT)
        assert is_transient_error(ErrorTypes.CONNECTION_REFUSED)
        assert is_transient_error(ErrorTypes.HOST_UNREACHABLE)
        assert is_transient_error(ErrorTypes.GATE_ERROR)

    def test_permanent_errors(self):
        assert is_permanent_error(ErrorTypes.AUTHENTICATION_FAILED)
        assert is_permanent_error(ErrorTypes.PERMISSION_DENIED)
        assert is_permanent_error(ErrorTypes.MODULE_NOT_FOUND)
        assert is_permanent_error(ErrorTypes.INVENTORY_ERROR)

    def test_transient_is_not_permanent(self):
        assert not is_permanent_error(ErrorTypes.CONNECTION_TIMEOUT)
        assert not is_transient_error(ErrorTypes.AUTHENTICATION_FAILED)

    def test_unknown_is_neither(self):
        assert not is_transient_error(ErrorTypes.UNKNOWN)
        assert not is_permanent_error(ErrorTypes.UNKNOWN)


class TestShouldRetry:
    def test_smart_retry_transient(self):
        assert should_retry(ErrorTypes.CONNECTION_TIMEOUT, smart_retry=True)

    def test_smart_retry_permanent(self):
        assert not should_retry(ErrorTypes.AUTHENTICATION_FAILED, smart_retry=True)

    def test_smart_retry_maybe_transient(self):
        assert should_retry(ErrorTypes.MODULE_EXECUTION_ERROR, smart_retry=True)
        assert should_retry(ErrorTypes.MODULE_TIMEOUT, smart_retry=True)
        assert should_retry(ErrorTypes.UNKNOWN, smart_retry=True)

    def test_dumb_retry_retries_most(self):
        assert should_retry(ErrorTypes.CONNECTION_TIMEOUT, smart_retry=False)
        assert should_retry(ErrorTypes.AUTHENTICATION_FAILED, smart_retry=False)
        assert should_retry(ErrorTypes.UNKNOWN, smart_retry=False)

    def test_dumb_retry_skips_module_not_found(self):
        assert not should_retry(ErrorTypes.MODULE_NOT_FOUND, smart_retry=False)
        assert not should_retry(ErrorTypes.INVENTORY_ERROR, smart_retry=False)


class TestRetryConfig:
    def test_defaults(self):
        config = RetryConfig()
        assert config.max_attempts == 0
        assert config.initial_delay == 5.0
        assert config.backoff_factor == 2.0

    def test_should_retry_error_with_smart(self):
        config = RetryConfig(smart_retry=True)
        assert config.should_retry_error(ErrorTypes.CONNECTION_TIMEOUT)
        assert not config.should_retry_error(ErrorTypes.AUTHENTICATION_FAILED)

    def test_should_retry_error_with_retry_on(self):
        config = RetryConfig(retry_on={ErrorTypes.AUTHENTICATION_FAILED})
        assert config.should_retry_error(ErrorTypes.AUTHENTICATION_FAILED)
        assert not config.should_retry_error(ErrorTypes.CONNECTION_TIMEOUT)

    def test_get_delay_first_attempt(self):
        config = RetryConfig(initial_delay=5.0)
        assert config.get_delay(1) == 5.0

    def test_get_delay_exponential_backoff(self):
        config = RetryConfig(initial_delay=5.0, backoff_factor=2.0, max_delay=60.0)
        delay = config.get_delay(3)
        # 5 * 2^2 = 20, ±10% jitter -> 18..22
        assert 17 < delay < 23

    def test_get_delay_capped_at_max(self):
        config = RetryConfig(initial_delay=10.0, backoff_factor=10.0, max_delay=30.0)
        delay = config.get_delay(5)
        # Should be capped at 30, ±10% jitter -> 27..33
        assert delay <= 33


class TestCircuitBreaker:
    def test_disabled(self):
        config = CircuitBreakerConfig(enabled=False)
        assert not check_circuit_breaker(10, 10, config)

    def test_below_min_hosts(self):
        config = CircuitBreakerConfig(enabled=True, min_hosts=5)
        assert not check_circuit_breaker(3, 3, config)

    def test_below_threshold(self):
        config = CircuitBreakerConfig(enabled=True, min_hosts=5, threshold_percent=30.0)
        assert not check_circuit_breaker(10, 2, config)

    def test_triggers_above_threshold(self):
        config = CircuitBreakerConfig(enabled=True, min_hosts=5, threshold_percent=30.0)
        assert check_circuit_breaker(10, 4, config)

    def test_zero_hosts(self):
        config = CircuitBreakerConfig(enabled=True, min_hosts=0)
        assert not check_circuit_breaker(0, 0, config)


class TestRetryState:
    def test_to_dict(self):
        state = RetryState(host_name="web01", attempts=3, succeeded=True)
        d = state.to_dict()
        assert d["host_name"] == "web01"
        assert d["attempts"] == 3
        assert d["succeeded"] is True
        assert d["gave_up"] is False


class TestRetryStats:
    def test_to_dict_total_retries(self):
        stats = RetryStats(
            total_hosts=3,
            succeeded_first_try=1,
            succeeded_after_retry=1,
            failed_after_retries=1,
        )
        stats.host_states["a"] = RetryState(host_name="a", attempts=1)
        stats.host_states["b"] = RetryState(host_name="b", attempts=3)
        stats.host_states["c"] = RetryState(host_name="c", attempts=2)
        d = stats.to_dict()
        assert d["total_retries"] == 3  # (0) + (2) + (1)

    def test_format_text_includes_retried_hosts(self):
        stats = RetryStats(succeeded_after_retry=1)
        stats.host_states["web01"] = RetryState(
            host_name="web01", attempts=3, succeeded=True
        )
        text = stats.format_text()
        assert "web01" in text
        assert "3 attempts" in text
        assert "succeeded" in text

    def test_format_text_circuit_breaker(self):
        stats = RetryStats(circuit_breaker_triggered=True)
        text = stats.format_text()
        assert "TRIGGERED" in text


class TestFormatRetrySummary:
    def test_empty(self):
        stats = RetryStats()
        assert format_retry_summary(stats) == ""

    def test_with_retries(self):
        stats = RetryStats(succeeded_after_retry=2, failed_after_retries=1)
        text = format_retry_summary(stats)
        assert "2 succeeded after retry" in text
        assert "1 failed after retries" in text

    def test_circuit_breaker(self):
        stats = RetryStats(circuit_breaker_triggered=True)
        text = format_retry_summary(stats)
        assert "circuit breaker triggered" in text


class TestClassifyErrorMessage:
    def test_timeout(self):
        from ftl2.retry import _classify_error_message

        assert _classify_error_message("Connection timeout") == ErrorTypes.CONNECTION_TIMEOUT

    def test_connection_refused(self):
        from ftl2.retry import _classify_error_message

        assert _classify_error_message("Connection refused") == ErrorTypes.CONNECTION_REFUSED

    def test_auth_failed(self):
        from ftl2.retry import _classify_error_message

        assert _classify_error_message("Authentication failed") == ErrorTypes.AUTHENTICATION_FAILED
        assert _classify_error_message("Invalid credentials") == ErrorTypes.AUTHENTICATION_FAILED

    def test_permission_denied(self):
        from ftl2.retry import _classify_error_message

        assert _classify_error_message("Permission denied") == ErrorTypes.PERMISSION_DENIED

    def test_unreachable(self):
        from ftl2.retry import _classify_error_message

        assert _classify_error_message("Host unreachable") == ErrorTypes.HOST_UNREACHABLE
        assert _classify_error_message("No route to host") == ErrorTypes.HOST_UNREACHABLE

    def test_module_not_found(self):
        from ftl2.retry import _classify_error_message

        assert _classify_error_message("Module xyz not found") == ErrorTypes.MODULE_NOT_FOUND

    def test_unknown(self):
        from ftl2.retry import _classify_error_message

        assert _classify_error_message("something went wrong") == ErrorTypes.UNKNOWN


class TestClassifyException:
    def test_timeout_exception(self):
        from ftl2.retry import _classify_exception

        class TimeoutError(Exception):
            pass

        assert _classify_exception(TimeoutError("oops")) == ErrorTypes.CONNECTION_TIMEOUT

    def test_connection_exception(self):
        from ftl2.retry import _classify_exception

        class ConnectionError(Exception):
            pass

        assert _classify_exception(ConnectionError("oops")) == ErrorTypes.CONNECTION_REFUSED

    def test_fallback_to_message(self):
        from ftl2.retry import _classify_exception

        assert _classify_exception(Exception("Permission denied")) == ErrorTypes.PERMISSION_DENIED


class TestRetryWithBackoff:
    async def test_success_first_try(self):
        from ftl2.retry import retry_with_backoff

        config = RetryConfig(max_attempts=3, initial_delay=0.01)

        async def ok():
            return "ok"

        result, state = await retry_with_backoff(ok, config, host_name="web01")
        assert result == "ok"
        assert state.succeeded
        assert state.attempts == 1
