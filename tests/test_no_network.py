"""Proves the safety net in conftest.py is switched on."""
import pytest
import requests


def test_a_real_request_from_a_test_is_refused():
    with pytest.raises(AssertionError, match="real network"):
        requests.get("https://example.com", timeout=1)
    with pytest.raises(AssertionError, match="real network"):
        requests.post("https://example.com", data={}, timeout=1)
