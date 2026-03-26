"""Tests for chunk() zero/negative guard — Issue #29.

Validates that chunk(lst, n) raises ValueError for n <= 0 with a
descriptive message, and that all existing positive-n behavior is preserved.
"""

import pytest

from ftl2.utils import chunk


class TestChunkZeroGuard:
    """Tests for the n <= 0 guard clause."""

    # --- Zero ---

    def test_zero_raises_valueerror(self):
        """chunk(lst, 0) must raise ValueError."""
        with pytest.raises(ValueError, match="Chunk size must be positive, got 0"):
            list(chunk([1, 2, 3], 0))

    def test_zero_with_empty_list(self):
        """chunk([], 0) must still raise — guard fires before iteration."""
        with pytest.raises(ValueError, match="got 0"):
            list(chunk([], 0))

    def test_zero_with_single_element(self):
        """chunk([x], 0) raises ValueError."""
        with pytest.raises(ValueError):
            list(chunk(["a"], 0))

    # --- Negative ---

    def test_negative_one_raises_valueerror(self):
        """chunk(lst, -1) must raise ValueError."""
        with pytest.raises(ValueError, match="Chunk size must be positive, got -1"):
            list(chunk([1, 2, 3], -1))

    def test_large_negative_raises_valueerror(self):
        """chunk(lst, -999) must raise ValueError."""
        with pytest.raises(ValueError, match="got -999"):
            list(chunk([1, 2], -999))

    def test_negative_with_empty_list(self):
        """chunk([], -1) raises — guard fires regardless of list contents."""
        with pytest.raises(ValueError):
            list(chunk([], -1))

    # --- Generator laziness ---

    def test_error_not_raised_until_iteration(self):
        """Calling chunk(lst, 0) alone returns a generator; error on iteration."""
        gen = chunk([1, 2, 3], 0)  # no error yet
        with pytest.raises(ValueError, match="Chunk size must be positive"):
            next(gen)

    def test_error_raised_on_list_conversion(self):
        """list(chunk(lst, 0)) triggers the error via iteration."""
        with pytest.raises(ValueError):
            list(chunk([1, 2, 3], 0))

    # --- Error message content ---

    def test_error_message_includes_value(self):
        """Error message must include the offending value."""
        with pytest.raises(ValueError) as exc_info:
            list(chunk([1], 0))
        assert "0" in str(exc_info.value)

    def test_error_message_includes_positive(self):
        """Error message must mention 'positive'."""
        with pytest.raises(ValueError) as exc_info:
            list(chunk([1], -5))
        assert "positive" in str(exc_info.value).lower()


class TestChunkPositiveBehavior:
    """Regression tests — existing positive-n behavior must be unchanged."""

    def test_even_division(self):
        assert list(chunk([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]

    def test_uneven_division(self):
        assert list(chunk([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]

    def test_size_larger_than_list(self):
        assert list(chunk([1, 2, 3], 10)) == [[1, 2, 3]]

    def test_empty_list(self):
        assert list(chunk([], 2)) == []

    def test_size_one(self):
        assert list(chunk([1, 2, 3], 1)) == [[1], [2], [3]]

    def test_single_element_list(self):
        assert list(chunk([42], 1)) == [[42]]

    def test_strings(self):
        """chunk works with lists of any type."""
        assert list(chunk(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]
