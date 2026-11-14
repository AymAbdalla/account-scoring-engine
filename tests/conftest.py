"""Safety net for every test: no test may reach the real network.

The tests use stand-ins for Salesforce. If a test (or the code it calls) ever
tries a real request, this makes it fail at once instead of touching the org.
"""
import pytest
import requests


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def refuse(self, method, url, *args, **kwargs):
        raise AssertionError("A test tried to reach the real network: {} {}".format(method, url))

    monkeypatch.setattr(requests.sessions.Session, "request", refuse)
