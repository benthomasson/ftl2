"""Pytest configuration and shared fixtures."""

import pytest


@pytest.fixture
def sample_fixture():
    """Example fixture for testing."""
    return {"example": "data"}
