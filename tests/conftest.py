"""Safety net for every test: no test may reach the real network, and no
test inherits what an earlier test told the engine to hide.

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


@pytest.fixture(autouse=True)
def forget_user_names():
    """The engine remembers the user names it has read, to keep them out of
    messages. Each test starts with none remembered."""
    from salesforce.client import USER_NAMES_TO_HIDE
    USER_NAMES_TO_HIDE.clear()
    yield
    USER_NAMES_TO_HIDE.clear()
