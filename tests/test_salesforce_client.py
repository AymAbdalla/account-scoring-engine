"""Tests for the code that logs in to Salesforce and reads the Account fields.

No test here touches the real Salesforce. A small stand-in plays its part and
returns the answers Salesforce would give. The key and secret are obvious fakes.
"""
import pytest
import requests

from salesforce.client import (
    SalesforceError,
    find_field_problems,
    log_in,
    read_account_fields,
    read_settings,
)

ENVIRONMENT = {
    "SF_INSTANCE_URL": "https://example-dev-ed.develop.my.salesforce.com/",
    "SF_CLIENT_ID": "test-client-id",
    "SF_CLIENT_SECRET": "test-client-secret",
}
SETTINGS = {
    "instance_url": "https://example-dev-ed.develop.my.salesforce.com",
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
}
SESSION = {"access_token": "test-token", "instance_url": "https://example-dev-ed.develop.my.salesforce.com"}


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


class FakeSalesforce:
    """Stands in for the network. Remembers what it was sent and replies with a set answer."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.sent = None

    def post(self, url, data=None, timeout=None):
        return self._reply({"url": url, "data": data, "timeout": timeout})

    def get(self, url, headers=None, timeout=None):
        return self._reply({"url": url, "headers": headers, "timeout": timeout})

    def _reply(self, sent):
        self.sent = sent
        if self.error:
            raise self.error
        return self.response


def test_settings_are_read_from_the_environment():
    # The trailing slash on the address is removed.
    assert read_settings(ENVIRONMENT) == SETTINGS


@pytest.mark.parametrize("missing", ["SF_INSTANCE_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET"])
def test_missing_setting_is_named_and_no_value_is_shown(missing):
    environment = dict(ENVIRONMENT)
    environment[missing] = ""
    with pytest.raises(SalesforceError) as caught:
        read_settings(environment)
    assert missing in str(caught.value)
    assert "test-client-secret" not in str(caught.value)


def test_org_address_must_start_with_https():
    with pytest.raises(SalesforceError, match="https://"):
        read_settings(dict(ENVIRONMENT, SF_INSTANCE_URL="http://example.my.salesforce.com"))


def test_login_asks_the_orgs_own_address_and_returns_the_token():
    salesforce = FakeSalesforce(FakeResponse(200, {
        "access_token": "test-token",
        "instance_url": "https://example-dev-ed.develop.my.salesforce.com",
    }))
    assert log_in(SETTINGS, http=salesforce) == SESSION
    assert salesforce.sent["url"] == "https://example-dev-ed.develop.my.salesforce.com/services/oauth2/token"
    assert salesforce.sent["data"] == {
        "grant_type": "client_credentials",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
    }
    assert salesforce.sent["timeout"] == 30


def test_rejected_login_says_why_without_showing_the_secret():
    salesforce = FakeSalesforce(FakeResponse(400, {
        "error": "invalid_client",
        "error_description": "invalid client credentials for test-client-secret",
    }))
    with pytest.raises(SalesforceError) as caught:
        log_in(SETTINGS, http=salesforce)
    assert "invalid_client" in str(caught.value)
    assert "test-client-secret" not in str(caught.value)


def test_login_answer_without_a_token_is_an_error():
    with pytest.raises(SalesforceError, match="no access token"):
        log_in(SETTINGS, http=FakeSalesforce(FakeResponse(200, {"instance_url": "https://x"})))


def test_login_answer_that_is_not_json_is_an_error():
    with pytest.raises(SalesforceError, match="HTTP 502"):
        log_in(SETTINGS, http=FakeSalesforce(FakeResponse(502, None)))


def test_unreachable_salesforce_is_reported_plainly():
    # The error text holds the secret on purpose: the engine must not repeat it.
    salesforce = FakeSalesforce(error=requests.exceptions.ConnectionError(
        "connection refused while sending client_secret=test-client-secret"))
    with pytest.raises(SalesforceError, match="Could not reach Salesforce") as caught:
        log_in(SETTINGS, http=salesforce)
    assert "test-client-secret" not in str(caught.value)


def test_account_fields_are_read_with_the_token():
    salesforce = FakeSalesforce(FakeResponse(200, {"fields": [
        {"name": "Score__c", "createable": True, "updateable": True},
        {"name": "Tier__c", "createable": True, "updateable": False},
    ]}))
    fields = read_account_fields(SESSION, http=salesforce)
    assert fields == {
        "Score__c": {"name": "Score__c", "createable": True, "updateable": True},
        "Tier__c": {"name": "Tier__c", "createable": True, "updateable": False},
    }
    assert salesforce.sent["url"] == (
        "https://example-dev-ed.develop.my.salesforce.com/services/data/v68.0/sobjects/Account/describe"
    )
    assert salesforce.sent["headers"] == {"Authorization": "Bearer test-token"}


def test_refused_field_read_is_an_error_without_the_token():
    salesforce = FakeSalesforce(FakeResponse(401, [{"errorCode": "INVALID_SESSION_ID", "message": "Session expired"}]))
    with pytest.raises(SalesforceError) as caught:
        read_account_fields(SESSION, http=salesforce)
    assert "INVALID_SESSION_ID" in str(caught.value)
    assert "test-token" not in str(caught.value)


def test_field_check_reports_missing_and_read_only_fields():
    fields = {
        "Score__c": {"name": "Score__c", "createable": True, "updateable": True},
        "Tier__c": {"name": "Tier__c", "createable": True, "updateable": False},
    }
    problems = find_field_problems(fields, ["Score__c", "Tier__c", "Segment__c"])
    assert len(problems) == 2
    assert "Tier__c" in problems[0] and "cannot edit" in problems[0]
    assert "Segment__c" in problems[1] and "cannot see" in problems[1]
    assert find_field_problems(fields, ["Score__c"]) == []


class ScriptedSalesforce:
    """Replies with a list of prepared answers, one per request, and remembers what was asked."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.sent.append({"url": url, "params": params})
        return self.responses.pop(0)


def test_existing_ids_are_read_page_by_page():
    from salesforce.client import find_existing_ids
    salesforce = ScriptedSalesforce([
        FakeResponse(200, {"done": False, "nextRecordsUrl": "/services/data/v68.0/query/01g-2000",
                           "records": [{"Sample_Id__c": "ACC-0001"}, {"Sample_Id__c": "ACC-0002"}]}),
        FakeResponse(200, {"done": True, "records": [{"Sample_Id__c": "ACC-0003"}]}),
    ])
    assert find_existing_ids(SESSION, http=salesforce) == {"ACC-0001", "ACC-0002", "ACC-0003"}
    assert salesforce.sent[0]["url"] == "https://example-dev-ed.develop.my.salesforce.com/services/data/v68.0/query"
    assert salesforce.sent[0]["params"] == {"q": "SELECT Sample_Id__c FROM Account WHERE Sample_Id__c != null"}
    assert salesforce.sent[1]["url"] == "https://example-dev-ed.develop.my.salesforce.com/services/data/v68.0/query/01g-2000"


def test_count_refuses_a_time_that_is_not_a_plain_utc_time():
    # The time goes into a query, so anything else is refused before any request.
    from salesforce.client import count_scored_at
    salesforce = ScriptedSalesforce([])
    with pytest.raises(SalesforceError, match="not a UTC time"):
        count_scored_at(SESSION, "2026-11-14T12:00:00Z OR Name != null", http=salesforce)
    assert salesforce.sent == []


def test_count_asks_for_the_exact_time_and_returns_the_total():
    from salesforce.client import count_scored_at
    salesforce = ScriptedSalesforce([FakeResponse(200, {"totalSize": 300, "done": True, "records": []})])
    assert count_scored_at(SESSION, "2026-11-14T12:00:00Z", http=salesforce) == 300
    assert salesforce.sent[0]["params"] == {"q": "SELECT COUNT() FROM Account WHERE Last_Scored__c = 2026-11-14T12:00:00Z"}


class WriteReply:
    """Answers one write request with a prepared reply."""

    def __init__(self, response):
        self.response = response

    def post(self, url, headers=None, json=None, timeout=None):
        return self.response


RECORDS = [
    {"attributes": {"type": "Account"}, "Sample_Id__c": "ACC-0001", "Score__c": 70},
    {"attributes": {"type": "Account"}, "Sample_Id__c": "ACC-0002", "Score__c": 20},
]
SAVED = {"id": "001", "success": True, "created": False, "errors": []}


@pytest.mark.parametrize(
    "response, words",
    [
        # An expired token: Salesforce answers 401 with a list, not the usual shape.
        (FakeResponse(401, [{"errorCode": "INVALID_SESSION_ID", "message": "Session expired or invalid"}]), "INVALID_SESSION_ID"),
        # A reply that covers none of the batches sent.
        (FakeResponse(200, {"compositeResponse": []}), "0 of 1 batch"),
        # A batch marked 200 but with one result for two records.
        (FakeResponse(200, {"compositeResponse": [{"httpStatusCode": 200, "body": [SAVED]}]}), "saved nothing"),
        # A batch marked 200 with no results at all.
        (FakeResponse(200, {"compositeResponse": [{"httpStatusCode": 200, "body": None}]}), "saved nothing"),
        # A batch marked 400 although every record says it was saved.
        (FakeResponse(200, {"compositeResponse": [{"httpStatusCode": 400, "body": [SAVED, SAVED]}]}), "saved nothing"),
        # A record that failed and gave no list of errors.
        (FakeResponse(200, {"compositeResponse": [{"httpStatusCode": 200, "body": [
            SAVED, {"success": False, "created": False, "errors": None}]}]}), "saved nothing"),
    ],
)
def test_write_reply_that_is_not_a_clean_success_is_an_error(response, words):
    from salesforce.client import write_records
    with pytest.raises(SalesforceError, match=words):
        write_records(SESSION, RECORDS, http=WriteReply(response))


def test_write_reply_with_every_record_saved_is_accepted():
    from salesforce.client import write_records
    reply = FakeResponse(200, {"compositeResponse": [{"httpStatusCode": 200, "body": [SAVED, SAVED]}]})
    assert write_records(SESSION, RECORDS, http=WriteReply(reply)) is None


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(200, {"done": True}),                       # no records in the answer
        FakeResponse(200, {"done": True, "records": "nonsense"}),
    ],
)
def test_existing_ids_answer_in_the_wrong_shape_is_an_error(response):
    from salesforce.client import find_existing_ids
    with pytest.raises(SalesforceError, match="existing accounts"):
        find_existing_ids(SESSION, http=ScriptedSalesforce([response]))


def test_existing_ids_follow_the_next_page_even_without_a_done_flag():
    from salesforce.client import find_existing_ids
    salesforce = ScriptedSalesforce([
        FakeResponse(200, {"nextRecordsUrl": "/services/data/v68.0/query/01g-2000", "records": [{"Sample_Id__c": "ACC-0001"}]}),
        FakeResponse(200, {"records": [{"Sample_Id__c": "ACC-0002"}, {"Sample_Id__c": None}]}),
    ])
    assert find_existing_ids(SESSION, http=salesforce) == {"ACC-0001", "ACC-0002"}


def test_count_answer_without_a_total_is_an_error():
    from salesforce.client import count_scored_at
    salesforce = ScriptedSalesforce([FakeResponse(200, {"done": True, "records": []})])
    with pytest.raises(SalesforceError, match="count the accounts"):
        count_scored_at(SESSION, "2026-11-14T12:00:00Z", http=salesforce)


# --- Replies with the wrong kind of value inside ---------------------------
# Whatever Salesforce sends, the engine must answer with its own plain error,
# never crash on a missing or odd value, and never read "not saved" as "saved".

@pytest.mark.parametrize(
    "result",
    [
        {"success": "false", "errors": []},   # text, not a real yes or no
        {"success": "true", "errors": []},
        {"success": 1, "errors": []},
        {"success": None, "errors": []},
        {"success": False, "errors": [None]},
        {"success": False, "errors": ["text"]},
        {"success": False, "errors": "text"},
        None,
        "saved",
    ],
)
def test_record_result_that_is_not_a_plain_yes_is_an_error(result):
    from salesforce.client import write_records
    reply = FakeResponse(200, {"compositeResponse": [{"httpStatusCode": 200, "body": [SAVED, result]}]})
    with pytest.raises(SalesforceError, match="saved nothing"):
        write_records(SESSION, RECORDS, http=WriteReply(reply))


@pytest.mark.parametrize(
    "parts",
    [[None], ["text"], [5], {"httpStatusCode": 200}, None],
)
def test_batch_answer_that_is_not_in_the_expected_shape_is_an_error(parts):
    from salesforce.client import write_records
    with pytest.raises(SalesforceError):
        write_records(SESSION, RECORDS, http=WriteReply(FakeResponse(200, {"compositeResponse": parts})))


@pytest.mark.parametrize(
    "records",
    [[None], ["text"], [{"Sample_Id__c": 5}], [{"Sample_Id__c": ["ACC-0001"]}]],
)
def test_existing_id_that_is_not_text_is_an_error(records):
    from salesforce.client import find_existing_ids
    with pytest.raises(SalesforceError, match="existing accounts"):
        find_existing_ids(SESSION, http=ScriptedSalesforce([FakeResponse(200, {"done": True, "records": records})]))


def test_next_page_address_that_is_not_text_is_an_error():
    from salesforce.client import find_existing_ids
    reply = FakeResponse(200, {"records": [], "nextRecordsUrl": 5})
    with pytest.raises(SalesforceError, match="existing accounts"):
        find_existing_ids(SESSION, http=ScriptedSalesforce([reply]))


@pytest.mark.parametrize("total", [True, "300", -1, 3.0, None, [300]])
def test_count_that_is_not_a_whole_number_is_an_error(total):
    from salesforce.client import count_scored_at
    salesforce = ScriptedSalesforce([FakeResponse(200, {"totalSize": total, "done": True, "records": []})])
    with pytest.raises(SalesforceError, match="count the accounts"):
        count_scored_at(SESSION, "2026-11-14T12:00:00Z", http=salesforce)


@pytest.mark.parametrize(
    "body",
    [{}, {"fields": None}, {"fields": [None]}, {"fields": [{"label": "No name"}]}, {"fields": [{"name": 5}]}],
)
def test_field_list_that_is_not_in_the_expected_shape_is_an_error(body):
    with pytest.raises(SalesforceError, match="describe Account"):
        read_account_fields(SESSION, http=FakeSalesforce(FakeResponse(200, body)))


@pytest.mark.parametrize("token", [123, ["test-token"], {"value": "x"}])
def test_login_token_that_is_not_text_is_an_error(token):
    with pytest.raises(SalesforceError, match="no access token"):
        log_in(SETTINGS, http=FakeSalesforce(FakeResponse(200, {"access_token": token})))


def test_login_answer_with_an_odd_org_address_falls_back_to_the_configured_one():
    reply = FakeResponse(200, {"access_token": "test-token", "instance_url": 5})
    assert log_in(SETTINGS, http=FakeSalesforce(reply)) == SESSION


# --- Nothing the engine prints may identify the org -------------------------
# Run logs on GitHub can be read by anyone once the repository is public. So a
# message built from a Salesforce reply, or from a network error, must never
# carry the org's address, a Salesforce id, a user name or a token. Here the
# stand-in puts all of those inside its replies; every one contains "mark".

ORG_HOST = "example-dev-ed.develop.my.salesforce.com"
MARKERS = [
    "https://marker-org.my.salesforce.com/services/data",  # a web address
    "marker-org.my.salesforce.com",                        # an org address without the https
    "001MARK00000123AAA",                                   # an 18-character record id
    "00DMARK00000123",                                      # a 15-character org id
    "marker.user@example.com",                              # a user name
    "00DMARK00000123!AQEAQmarkSessionToken.x_y",            # a session token
]
LEAK = "Problem for " + " and ".join(MARKERS)
LEAK_AT_LOGIN = LEAK + " with test-client-id and test-client-secret"  # what the engine holds at login
LEAK_LATER = LEAK + " using test-token"                                # what it holds after login


def assert_identifies_nothing(text):
    assert "mark" not in text.lower()
    for value in [ORG_HOST, "salesforce.com", "https://", "test-client-id", "test-client-secret", "test-token"]:
        assert value not in text


def refused_login():
    reply = FakeResponse(400, {"error": "invalid_grant", "error_description": LEAK_AT_LOGIN})
    log_in(SETTINGS, http=FakeSalesforce(reply))


def refused_field_read():
    reply = FakeResponse(401, [{"errorCode": "INVALID_SESSION_ID", "message": LEAK_LATER}])
    read_account_fields(SESSION, http=FakeSalesforce(reply))


def refused_list_of_accounts():
    from salesforce.client import find_existing_ids
    reply = FakeResponse(503, [{"errorCode": "SERVER_UNAVAILABLE", "message": LEAK_LATER}])
    find_existing_ids(SESSION, http=ScriptedSalesforce([reply]))


def refused_count():
    from salesforce.client import count_scored_at
    reply = FakeResponse(400, [{"errorCode": "MALFORMED_QUERY", "message": LEAK_LATER}])
    count_scored_at(SESSION, "2026-11-14T12:00:00Z", http=ScriptedSalesforce([reply]))


def refused_write_request():
    from salesforce.client import write_records
    reply = FakeResponse(400, [{"errorCode": "INVALID_FIELD", "message": LEAK_LATER}])
    write_records(SESSION, RECORDS, http=WriteReply(reply))


def refused_batch():
    from salesforce.client import write_records
    reply = FakeResponse(200, {"compositeResponse": [{
        "httpStatusCode": 400, "body": [{"errorCode": "INVALID_FIELD", "message": LEAK_LATER}]}]})
    write_records(SESSION, RECORDS, http=WriteReply(reply))


def refused_record():
    from salesforce.client import write_records
    bad = {"success": False, "created": False,
           "errors": [{"statusCode": "DUPLICATE_VALUE", "message": LEAK_LATER, "fields": []}]}
    reply = FakeResponse(200, {"compositeResponse": [{"httpStatusCode": 200, "body": [SAVED, bad]}]})
    write_records(SESSION, RECORDS, http=WriteReply(reply))


@pytest.mark.parametrize(
    "refusal, still_says",
    [
        (refused_login, "invalid_grant"),
        (refused_field_read, "INVALID_SESSION_ID"),
        (refused_list_of_accounts, "SERVER_UNAVAILABLE"),
        (refused_count, "MALFORMED_QUERY"),
        (refused_write_request, "INVALID_FIELD"),
        (refused_batch, "INVALID_FIELD"),
        (refused_record, "ACC-0002"),
    ],
)
def test_refusal_keeps_salesforces_error_code_and_identifies_nothing(refusal, still_says):
    with pytest.raises(SalesforceError) as caught:
        refusal()
    assert still_says in str(caught.value)
    assert "Problem for" in str(caught.value)  # Salesforce's own words are kept
    assert_identifies_nothing(str(caught.value))


@pytest.mark.parametrize(
    "body",
    [
        {"unexpected": LEAK},        # a reply in a shape the engine does not know
        [{"detail": LEAK}],
        LEAK,
    ],
)
def test_refusal_in_an_unknown_shape_is_reported_by_its_status_only(body):
    with pytest.raises(SalesforceError, match="HTTP 500") as caught:
        read_account_fields(SESSION, http=FakeSalesforce(FakeResponse(500, body)))
    assert_identifies_nothing(str(caught.value))


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError("could not connect to " + LEAK),
        requests.exceptions.InvalidURL("bad address " + LEAK),
    ],
)
@pytest.mark.parametrize("request_made", ["login", "field read"])
def test_network_failure_names_neither_the_org_nor_the_request(error, request_made):
    salesforce = FakeSalesforce(error=error)
    with pytest.raises(SalesforceError, match="Could not reach Salesforce") as caught:
        if request_made == "login":
            log_in(SETTINGS, http=salesforce)
        else:
            read_account_fields(SESSION, http=salesforce)
    assert type(error).__name__ in str(caught.value)
    assert_identifies_nothing(str(caught.value))


def test_salesforce_text_cannot_start_a_line_or_run_on_without_end():
    # GitHub reads a printed line that starts with "::" as a command to itself.
    message = "first line\n::add-mask::something\n" + "long " * 2000
    reply = FakeResponse(401, [{"errorCode": "INVALID_SESSION_ID", "message": message}])
    with pytest.raises(SalesforceError) as caught:
        read_account_fields(SESSION, http=FakeSalesforce(reply))
    text = str(caught.value)
    assert "INVALID_SESSION_ID" in text and "first line" in text
    assert not any(line.lstrip().startswith("::") for line in text.splitlines())
    assert len(text) < 1000


def test_field_names_and_plain_words_in_salesforces_text_are_kept():
    # Only what identifies the org is removed. "Account.Name" has a dot but is not an address.
    reply = FakeResponse(400, [{"errorCode": "STRING_TOO_LONG", "message": "Account.Name: data value too large (max length=255)"}])
    with pytest.raises(SalesforceError) as caught:
        read_account_fields(SESSION, http=FakeSalesforce(reply))
    assert "STRING_TOO_LONG: Account.Name: data value too large (max length=255)" in str(caught.value)


def test_settings_echoed_back_after_login_are_still_hidden():
    # After the login the engine no longer passes its settings around, but a
    # later reply could still repeat the client id or the secret.
    logged_in = FakeResponse(200, {"access_token": "test-token", "instance_url": "https://" + ORG_HOST})
    session = log_in(SETTINGS, http=FakeSalesforce(logged_in))
    echo = "Refused for client test-client-id with secret test-client-secret"
    reply = FakeResponse(401, [{"errorCode": "INVALID_SESSION_ID", "message": echo}])
    with pytest.raises(SalesforceError) as caught:
        read_account_fields(session, http=FakeSalesforce(reply))
    assert "INVALID_SESSION_ID: Refused for client" in str(caught.value)
    assert_identifies_nothing(str(caught.value))
