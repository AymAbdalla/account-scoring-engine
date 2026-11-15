"""Tests for the connection check: what it prints, and what it must not print.

No test here touches the real Salesforce. A small stand-in answers the login
and the field list. The address, key and secret are obvious fakes.
"""
import pytest

from check_salesforce import main
from salesforce.records import load_field_limits

ENVIRONMENT = {
    "SF_INSTANCE_URL": "https://example-dev-ed.develop.my.salesforce.com",
    "SF_CLIENT_ID": "test-client-id",
    "SF_CLIENT_SECRET": "test-client-secret",
}
LEAK = "Problem at https://marker-org.my.salesforce.com for marker.user@example.com, record 001MARK00000123AAA"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body


class FakeOrg:
    """Answers the login, then the request for Account's fields."""

    def __init__(self, login=None, fields=None, crash=None, fields_refusal=None, owner=None, users_refusal=None):
        self.fields_refusal = fields_refusal
        self.users_refusal = users_refusal
        self.login = login or FakeResponse(200, {
            "access_token": "test-token",
            "instance_url": "https://marker-org.my.salesforce.com",
        })
        names = sorted(load_field_limits()) if fields is None else fields
        # The standard Account Owner field: settable on create, as read from the real org.
        owner = {"name": "OwnerId", "createable": True, "updateable": True} if owner is None else owner
        self.fields = FakeResponse(200, {"fields": [
            {"name": name, "createable": True, "updateable": True} for name in names] + ([owner] if owner else [])})
        self.crash = crash

    def post(self, url, data=None, timeout=None):
        if self.crash:
            raise self.crash
        return self.login

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/query"):
            return self.users_refusal or FakeResponse(200, {"done": True, "records": [
                {"Id": "005MARK00000123AAA", "Name": "Marker Person"}]})
        return self.fields_refusal or self.fields


def assert_identifies_nothing(text):
    assert "mark" not in text.lower()
    for value in ["example-dev-ed", "salesforce.com", "https://", "test-client-id", "test-client-secret", "test-token"]:
        assert value not in text


def test_check_reports_ten_fields_without_showing_the_org_address(capsys):
    main(environ=ENVIRONMENT, http=FakeOrg())
    printed = capsys.readouterr().out
    assert "Logged in." in printed
    assert "All 10 fields are found and editable." in printed
    assert_identifies_nothing(printed)


def test_check_names_a_field_the_engine_cannot_see():
    names = [name for name in sorted(load_field_limits()) if name != "Tier__c"]
    with pytest.raises(SystemExit) as stopped:
        main(environ=ENVIRONMENT, http=FakeOrg(fields=names))
    assert "Salesforce check failed" in str(stopped.value.code)
    assert "Tier__c: the engine's user cannot see this field" in str(stopped.value.code)


def test_refused_login_ends_with_an_error_that_identifies_nothing(capsys):
    refusal = FakeResponse(400, {"error": "invalid_grant", "error_description": LEAK})
    with pytest.raises(SystemExit) as stopped:
        main(environ=ENVIRONMENT, http=FakeOrg(login=refusal))
    assert "Salesforce check failed" in str(stopped.value.code)
    assert "invalid_grant" in str(stopped.value.code)
    assert_identifies_nothing(str(stopped.value.code) + capsys.readouterr().out)


def test_error_nobody_expected_ends_with_its_type_only(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(environ=ENVIRONMENT, http=FakeOrg(crash=RuntimeError(LEAK)))
    assert "Salesforce check failed" in str(stopped.value.code)
    assert "RuntimeError" in str(stopped.value.code)
    assert_identifies_nothing(str(stopped.value.code) + capsys.readouterr().out)


def test_settings_echoed_back_after_login_are_still_hidden(capsys):
    echo = "Refused for client test-client-id with secret test-client-secret"
    refusal = FakeResponse(401, [{"errorCode": "INVALID_SESSION_ID", "message": echo}])
    with pytest.raises(SystemExit) as stopped:
        main(environ=ENVIRONMENT, http=FakeOrg(fields_refusal=refusal))
    assert "INVALID_SESSION_ID: Refused for client" in str(stopped.value.code)
    assert_identifies_nothing(str(stopped.value.code) + capsys.readouterr().out)


# Account ownership: the engine sets the owner when it first creates an
# account, so the check also looks at the Account Owner field and at whether
# the engine can read the users' names. It reads only.

def test_check_reports_that_the_owner_can_be_set_and_user_names_can_be_read(capsys):
    main(environ=ENVIRONMENT, http=FakeOrg())
    printed = capsys.readouterr().out
    assert "Account Owner can be set when an account is created." in printed
    assert "The names of the active users can be read." in printed
    assert_identifies_nothing(printed)  # no user's name or id is shown


def test_check_fails_when_the_owner_cannot_be_set_on_create():
    owner = {"name": "OwnerId", "createable": False, "updateable": False}
    with pytest.raises(SystemExit) as stopped:
        main(environ=ENVIRONMENT, http=FakeOrg(owner=owner))
    assert "Salesforce check failed" in str(stopped.value.code)
    assert "OwnerId" in str(stopped.value.code) and "cannot set" in str(stopped.value.code)


def test_check_fails_when_the_user_names_cannot_be_read(capsys):
    refusal = FakeResponse(403, [{"errorCode": "INSUFFICIENT_ACCESS", "message": LEAK}])
    with pytest.raises(SystemExit) as stopped:
        main(environ=ENVIRONMENT, http=FakeOrg(users_refusal=refusal))
    assert "would not list the active users" in str(stopped.value.code)
    assert_identifies_nothing(str(stopped.value.code) + capsys.readouterr().out)
