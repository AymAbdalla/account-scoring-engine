"""Tests for the write-back: score, check, write to Salesforce, read back.

No test here touches the real Salesforce. FakeOrg below plays its part: it keeps
accounts in memory and answers the three requests the engine makes, in the same
shapes the real org returned in a test on 2026-11-14 (including undoing
everything when one record in a request is bad).
"""
import json
import re

import pytest
import requests

from salesforce.records import load_field_limits
from scoring.engine import load_rules
from write_back import WriteBackFailed, main, write_back

RULES = load_rules()
LIMITS = load_field_limits()
SESSION = {"access_token": "test-token", "instance_url": "https://example-dev-ed.develop.my.salesforce.com"}
ROSTER = {"Sample Person": {"segment": "SMB", "region": "North America"}}


def make_account(number, **changes):
    account = {
        "id": "ACC-{:04d}".format(number),
        "name": "Sample Company {}".format(number),
        "hq_country": "United States",
        "bdr": "Sample Person",
        "employees": 100,
        "countries": 5,
        "industry": "retail",
        "hiring_surge": True,
        "new_hr_leader": False,
        "days_since_engaged": 60,
    }
    account.update(changes)
    return account


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body


class FakeOrg:
    """An in-memory Salesforce org with only what the engine uses.

    It is as strict as the real org about the request it is sent: the wrong
    address, method or record shape fails the test, as it would fail for real.
    """

    UPSERT_ADDRESS = "/services/data/v68.0/composite/sobjects/Account/Sample_Id__c"

    def __init__(self):
        self.accounts = {}          # Sample Id -> the fields saved on that account
        self.requests = []          # every request received: ("query" / "count" / "write", detail)
        self.reject = {}            # Sample Id -> error message Salesforce should give for it
        self.count_script = []      # answers for the next count requests, in order: a number, or a FakeResponse
        self.time_out_on_write = None  # "before saving" or "after saving"
        self.time_out_only_on_write_number = None  # 2 means only the second write gets no answer; None means every write
        self.before_write = None    # something to do when a write arrives, before handling it
        self.login_reply = None     # a FakeResponse to give instead of a token
        self.query_reply = None     # a FakeResponse to give instead of the list of existing ids
        self.write_reply = None     # a FakeResponse to give instead of doing the write
        self.users = []             # the active users: {"Id": ..., "Name": ...}
        self.users_reply = None     # a FakeResponse to give instead of the list of active users

    def stored_id(self, sample_id):
        """Find an account ignoring upper and lower case, as the real Sample Id field does."""
        for stored in self.accounts:
            if stored.lower() == sample_id.lower():
                return stored
        return None

    def get(self, url, headers=None, params=None, timeout=None):
        assert headers == {"Authorization": "Bearer test-token"}
        assert url == "https://example-dev-ed.develop.my.salesforce.com/services/data/v68.0/query"
        assert timeout == 30
        query = params["q"]
        if "COUNT()" in query:
            scored_at = re.search(r"^SELECT COUNT\(\) FROM Account WHERE Last_Scored__c = (\S+)$", query).group(1)
            self.requests.append(("count", scored_at))
            total = sum(1 for fields in self.accounts.values() if fields.get("Last_Scored__c") == scored_at)
            if self.count_script:
                scripted = self.count_script.pop(0)
                if isinstance(scripted, FakeResponse):
                    return scripted
                total = scripted
            return FakeResponse(200, {"totalSize": total, "done": True, "records": []})
        if query == "SELECT Id, Name FROM User WHERE IsActive = true":
            self.requests.append(("users", query))
            return self.users_reply or FakeResponse(200, {"totalSize": len(self.users), "done": True, "records": self.users})
        assert query == "SELECT Sample_Id__c FROM Account WHERE Sample_Id__c != null"
        self.requests.append(("query", query))
        if self.query_reply:
            return self.query_reply
        return FakeResponse(200, {
            "totalSize": len(self.accounts),
            "done": True,
            "records": [{"Sample_Id__c": sample_id} for sample_id in self.accounts],
        })

    def post(self, url, data=None, headers=None, json=None, timeout=None):
        assert timeout == 30
        if url == "https://example-dev-ed.develop.my.salesforce.com/services/oauth2/token":
            assert data["grant_type"] == "client_credentials"
            return self.login_reply or FakeResponse(200, {
                "access_token": "test-token",
                "instance_url": "https://example-dev-ed.develop.my.salesforce.com",
            })
        assert headers == {"Authorization": "Bearer test-token"}
        assert url == "https://example-dev-ed.develop.my.salesforce.com/services/data/v68.0/composite"
        assert json["allOrNone"] is True
        for part in json["compositeRequest"]:
            assert part["method"] == "PATCH"
            assert part["url"] == self.UPSERT_ADDRESS
            assert part["body"]["allOrNone"] is True
            assert 1 <= len(part["body"]["records"]) <= 200
            for record in part["body"]["records"]:
                assert record["attributes"] == {"type": "Account"}
        batches = [part["body"]["records"] for part in json["compositeRequest"]]
        self.requests.append(("write", [len(batch) for batch in batches]))
        if self.before_write:
            self.before_write()
        write_number = [kind for kind, _ in self.requests].count("write")
        timing_out = self.time_out_on_write if self.time_out_only_on_write_number in (None, write_number) else None
        if timing_out == "before saving":
            # The text holds the token on purpose: the engine must not repeat it.
            raise requests.exceptions.Timeout("no answer to request with Bearer test-token")
        if self.write_reply:
            return self.write_reply

        def bad(record):
            return record["Sample_Id__c"] in self.reject

        if any(bad(record) for batch in batches for record in batch):
            # One bad record: Salesforce saves nothing from any batch.
            parts = []
            for number, batch in enumerate(batches, start=1):
                if any(bad(record) for record in batch):
                    results = []
                    for record in batch:
                        if bad(record):
                            error = {"statusCode": "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST",
                                     "message": self.reject[record["Sample_Id__c"]], "fields": ["Tier__c"]}
                        else:
                            error = {"statusCode": "ALL_OR_NONE_OPERATION_ROLLED_BACK",
                                     "message": "Record rolled back because not all records were valid and the request was using AllOrNone header",
                                     "fields": []}
                        results.append({"success": False, "created": True, "errors": [error]})
                    parts.append({"httpStatusCode": 200, "referenceId": "batch{}".format(number), "body": results})
                else:
                    parts.append({"httpStatusCode": 400, "referenceId": "batch{}".format(number), "body": [{
                        "errorCode": "PROCESSING_HALTED",
                        "message": "The transaction was rolled back since another operation in the same transaction failed.",
                    }]})
            return FakeResponse(200, {"compositeResponse": parts})

        parts = []
        for number, batch in enumerate(batches, start=1):
            results = []
            for record in batch:
                fields = {key: value for key, value in record.items() if key not in ("attributes", "Sample_Id__c")}
                stored = self.stored_id(record["Sample_Id__c"])
                if stored is None:
                    assert record.get("Name"), "Salesforce requires a Name to create an account"
                    stored = record["Sample_Id__c"]
                    self.accounts[stored] = {"Sample_Id__c": stored}
                    created = True
                else:
                    created = False
                self.accounts[stored].update(fields)
                results.append({"id": "001", "success": True, "created": created, "errors": []})
            parts.append({"httpStatusCode": 200, "referenceId": "batch{}".format(number), "body": results})
        if timing_out == "after saving":
            raise requests.exceptions.Timeout("no answer to request with Bearer test-token")
        return FakeResponse(200, {"compositeResponse": parts})


def run(org, accounts, scored_at=None):
    """Run the write-back against the fake org, keeping what it prints.

    scored_at freezes the clock: the run uses exactly that time.
    """
    printed = []
    result = write_back(accounts, RULES, ROSTER, LIMITS, SESSION, http=org, say=printed.append, scored_at=scored_at)
    return result, printed


def test_first_run_creates_every_account_with_every_field():
    org = FakeOrg()
    result, printed = run(org, [make_account(1), make_account(2), make_account(3)])
    assert result == {"scored": 3, "written": 3, "failed": 0}
    assert sorted(org.accounts) == ["ACC-0001", "ACC-0002", "ACC-0003"]
    saved = org.accounts["ACC-0001"]
    assert saved["Name"] == "Sample Company 1"
    assert saved["BDR__c"] == "Sample Person"
    assert (saved["Score__c"], saved["Tier__c"], saved["Segment__c"], saved["Region__c"]) == (70, "B", "SMB", "North America")
    assert saved["Territory_Status__c"] == "In territory"
    assert any("Scored 3, written 3, failed 0" in line for line in printed)


def test_second_run_updates_in_place_and_keeps_a_hand_edited_bdr_and_name():
    org = FakeOrg()
    accounts = [make_account(1), make_account(2)]
    run(org, accounts, scored_at="2026-11-14T12:00:00Z")
    # Someone edits the account in Salesforce between runs.
    org.accounts["ACC-0001"]["BDR__c"] = "Edited By Hand"
    org.accounts["ACC-0001"]["Name"] = "Renamed By Hand"
    org.accounts["ACC-0001"]["Last_Scored__c"] = "2020-01-01T00:00:00Z"
    org.accounts["ACC-0001"]["Score__c"] = 1

    result, _ = run(org, accounts, scored_at="2026-11-14T12:00:05Z")  # a later run
    assert result == {"scored": 2, "written": 2, "failed": 0}
    assert sorted(org.accounts) == ["ACC-0001", "ACC-0002"]      # no duplicates
    assert org.accounts["ACC-0001"]["BDR__c"] == "Edited By Hand"  # not overwritten
    assert org.accounts["ACC-0001"]["Name"] == "Renamed By Hand"   # not overwritten
    assert org.accounts["ACC-0001"]["Score__c"] == 70              # the engine's field is refreshed
    assert org.accounts["ACC-0001"]["Last_Scored__c"] == "2026-11-14T12:00:05Z"


def test_one_bad_record_stops_the_run_before_anything_is_sent():
    # Guard under test: find_problems, run before any request. The duplicate id
    # is caught there; nothing reaches the fake org.
    org = FakeOrg()
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2), make_account(2)])
    assert "ACC-0002" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (3, 0, 3)
    assert org.requests == []
    assert org.accounts == {}


def test_record_rejected_by_salesforce_saves_nothing_and_names_the_account():
    org = FakeOrg()
    org.reject["ACC-0002"] = "Tier: bad value for restricted picklist field: B"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2), make_account(3)])
    assert "ACC-0002" in str(caught.value)
    assert "bad value for restricted picklist" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (3, 0, 3)
    assert org.accounts == {}


def test_read_back_that_comes_up_short_fails_the_run():
    org = FakeOrg()
    org.count_script = [0, 2]  # none before the write, then only 2 of the 3 after it
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2), make_account(3)])
    assert "read back" in str(caught.value).lower()
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (3, 2, 1)


def test_more_than_200_records_go_in_one_request_split_into_batches():
    # One request is the requirement: Salesforce undoes all batches in a
    # request together, but not batches sent in separate requests.
    org = FakeOrg()
    result, _ = run(org, [make_account(number) for number in range(1, 251)])
    assert result == {"scored": 250, "written": 250, "failed": 0}
    assert len(org.accounts) == 250
    assert [detail for kind, detail in org.requests if kind == "write"] == [[200, 50]]


def test_write_that_times_out_without_saving_fails_the_run():
    org = FakeOrg()
    org.time_out_on_write = "before saving"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, 0, 2)
    message = str(caught.value)
    assert "no answer" in message.lower()
    assert "No account carries this run's time, so nothing was written." in message
    assert "Run it again; a rerun is safe." in message
    assert "test-token" not in message


def test_write_that_gets_no_answer_is_never_reported_as_a_success():
    # No answer came back. Here the write was in fact saved, and every account
    # carries this run's time. But a count cannot prove the accounts came from
    # THIS run (another run in the same second would look the same), so the
    # engine reports that it does not know, and the run fails.
    org = FakeOrg()
    org.time_out_on_write = "after saving"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, None, None)
    assert "no answer" in str(caught.value).lower()
    assert "run it again" in str(caught.value).lower()
    assert "test-token" not in str(caught.value)


def test_one_record_more_than_a_request_can_hold_goes_in_a_second_request():
    # SPEC.md, write-back rule 4: one all-or-nothing request holds at most
    # 1,000 records (5 batches of 200), and a larger run is sent as several.
    org = FakeOrg()
    result, _ = run(org, [make_account(number) for number in range(1, 1002)])
    assert result == {"scored": 1001, "written": 1001, "failed": 0}
    assert [detail for kind, detail in org.requests if kind == "write"] == [[200, 200, 200, 200, 200], [1]]


def test_no_accounts_at_all_is_a_failure_not_a_silent_success():
    org = FakeOrg()
    with pytest.raises(WriteBackFailed, match="no accounts to write"):
        run(org, [])
    assert org.requests == []


def test_bad_record_late_in_the_second_batch_is_the_one_named():
    # 250 records travel as batches of 200 and 50. The bad one is deep in the
    # second batch, behind dozens of "rolled back" results that are not the cause.
    org = FakeOrg()
    org.reject["ACC-0240"] = "Tier: bad value for restricted picklist field: B"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(number) for number in range(1, 251)])
    assert "ACC-0240: Tier: bad value for restricted picklist field: B" in str(caught.value)
    assert "rolled back" not in str(caught.value).lower()
    assert org.accounts == {}


def test_batch_refused_as_a_whole_shows_salesforces_own_reason():
    # The shape Salesforce uses when a whole batch is refused, for example for a missing field.
    org = FakeOrg()
    org.write_reply = FakeResponse(200, {"compositeResponse": [{
        "httpStatusCode": 400, "referenceId": "batch1",
        "body": [{"errorCode": "INVALID_FIELD", "message": "No such column 'Foo__c' on sobject of type Account"}],
    }]})
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert "INVALID_FIELD: No such column 'Foo__c' on sobject of type Account" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, 0, 2)


def test_read_back_that_finds_too_many_fails_without_a_negative_count():
    org = FakeOrg()
    org.count_script = [0, 5]  # none before the write, then 5 after it
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2), make_account(3)])
    assert "found 5" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (3, 5, 0)


def test_read_back_that_cannot_be_done_reports_the_counts_as_unknown():
    # The write went through, but the count afterwards was refused. The engine
    # cannot say how many were written, so it must not claim zero.
    org = FakeOrg()
    org.count_script = [0, FakeResponse(401, [{"errorCode": "INVALID_SESSION_ID", "message": "Session expired or invalid"}])]
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert "INVALID_SESSION_ID" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, None, None)


def test_failure_to_list_existing_accounts_keeps_the_scored_count_and_sends_no_write():
    org = FakeOrg()
    org.query_reply = FakeResponse(503, [{"errorCode": "SERVER_UNAVAILABLE", "message": "Try again later"}])
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2), make_account(3)])
    assert "SERVER_UNAVAILABLE" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (3, 0, 3)
    assert [kind for kind, _ in org.requests] == ["query"]


def test_account_stored_with_different_letter_case_is_still_treated_as_existing():
    # Salesforce treats acc-0001 and ACC-0001 as the same Sample Id. The engine
    # must too, or it would resend the Name and BDR and overwrite a hand edit.
    org = FakeOrg()
    org.accounts["acc-0001"] = {"Sample_Id__c": "acc-0001", "Name": "Renamed By Hand", "BDR__c": "Edited By Hand"}
    result, _ = run(org, [make_account(1)])
    assert result == {"scored": 1, "written": 1, "failed": 0}
    assert list(org.accounts) == ["acc-0001"]
    assert org.accounts["acc-0001"]["Name"] == "Renamed By Hand"
    assert org.accounts["acc-0001"]["BDR__c"] == "Edited By Hand"
    assert org.accounts["acc-0001"]["Score__c"] == 70


# --- The whole program, as it runs from the command line -------------------

ENVIRONMENT = {
    "SF_INSTANCE_URL": "https://example-dev-ed.develop.my.salesforce.com",
    "SF_CLIENT_ID": "test-client-id",
    "SF_CLIENT_SECRET": "test-client-secret",
}


def write_accounts_file(folder, content):
    path = folder / "accounts.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def test_program_logs_in_writes_and_prints_the_counts(tmp_path, capsys):
    org = FakeOrg()
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    main(path, environ=ENVIRONMENT, http=org)
    assert "Scored 2, written 2, failed 0." in capsys.readouterr().out
    assert sorted(org.accounts) == ["ACC-0001", "ACC-0002"]


def test_program_ends_with_an_error_and_the_counts_when_salesforce_rejects_a_record(tmp_path, capsys):
    org = FakeOrg()
    org.reject["ACC-0002"] = "Tier: bad value for restricted picklist field: B"
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code) and "ACC-0002" in str(stopped.value.code)
    assert "Scored 2, written 0, failed 2." in capsys.readouterr().out


def test_program_says_the_counts_are_unknown_when_the_read_back_cannot_be_done(tmp_path, capsys):
    org = FakeOrg()
    org.count_script = [0, FakeResponse(401, [{"errorCode": "INVALID_SESSION_ID", "message": "Session expired or invalid"}])]
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code)
    assert "Scored 2, written unknown, failed unknown." in capsys.readouterr().out


def test_program_ends_with_an_error_when_the_login_is_refused(tmp_path, capsys):
    org = FakeOrg()
    org.login_reply = FakeResponse(400, {"error": "invalid_client", "error_description": "invalid client credentials"})
    path = write_accounts_file(tmp_path, [make_account(1)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code) and "invalid_client" in str(stopped.value.code)
    printed = capsys.readouterr().out
    assert "Nothing was scored or written." in printed
    assert "failed 0" not in printed
    assert org.requests == []


@pytest.mark.parametrize(
    "content, words",
    [
        ("{not json", "not valid JSON"),
        ({"accounts": []}, "a list of accounts"),
        ([{"name": "No Id Company"}], "a list of accounts"),
        (["just text"], "a list of accounts"),
    ],
)
def test_program_explains_a_data_file_it_cannot_use_and_sends_nothing(tmp_path, capsys, content, words):
    org = FakeOrg()
    path = write_accounts_file(tmp_path, content)
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code) and words in str(stopped.value.code)
    assert "Nothing was scored or written." in capsys.readouterr().out
    assert org.requests == []


# --- One second, two runs: an old run must never vouch for a new one -------

FROZEN = "2026-11-14T12:00:00Z"


def lower_scores(accounts):
    """The same accounts with the hiring surge gone, so they score 50, not 70."""
    return [dict(account, hiring_surge=False) for account in accounts]


def stored_scores(org):
    return sorted(set(fields["Score__c"] for fields in org.accounts.values()))


def test_rejected_run_is_not_vouched_for_by_an_earlier_run_in_the_same_second():
    org = FakeOrg()
    accounts = [make_account(number) for number in range(1, 301)]
    run(org, accounts, scored_at=FROZEN)
    assert stored_scores(org) == [70]

    # A second run in the same second, with new scores, that Salesforce rejects.
    org.reject["ACC-0150"] = "Tier: bad value for restricted picklist field: C"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, lower_scores(accounts), scored_at=FROZEN)
    assert caught.value.scored == 300
    assert caught.value.written == 0
    assert stored_scores(org) == [70]  # the old scores are still there, and the run said so


def test_timed_out_run_is_not_vouched_for_by_an_earlier_run_in_the_same_second():
    org = FakeOrg()
    accounts = [make_account(number) for number in range(1, 301)]
    run(org, accounts, scored_at=FROZEN)

    org.time_out_on_write = "before saving"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, lower_scores(accounts), scored_at=FROZEN)
    assert caught.value.scored == 300
    assert caught.value.written == 0
    assert stored_scores(org) == [70]


def test_run_stops_before_writing_when_accounts_already_carry_its_time():
    # If accounts already carry this run's time, a count afterwards could not
    # tell this run's work from the earlier run's. So nothing is sent.
    org = FakeOrg()
    accounts = [make_account(1), make_account(2)]
    run(org, accounts, scored_at=FROZEN)
    writes_before = [kind for kind, _ in org.requests].count("write")
    with pytest.raises(WriteBackFailed) as caught:
        run(org, lower_scores(accounts), scored_at=FROZEN)
    assert "already carry this run's time" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, 0, 2)
    assert [kind for kind, _ in org.requests].count("write") == writes_before
    assert stored_scores(org) == [70]


def test_run_counts_before_writing_and_after():
    org = FakeOrg()
    run(org, [make_account(1), make_account(2)], scored_at=FROZEN)
    assert [kind for kind, _ in org.requests] == ["query", "count", "write", "count"]


def test_count_before_writing_that_cannot_be_done_sends_no_write():
    org = FakeOrg()
    org.count_script = [FakeResponse(503, [{"errorCode": "SERVER_UNAVAILABLE", "message": "Try again later"}])]
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert "SERVER_UNAVAILABLE" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, 0, 2)
    assert [kind for kind, _ in org.requests] == ["query", "count"]


def test_rejection_by_salesforce_fails_the_run_even_if_the_count_matches():
    # Salesforce said no. A count that happens to equal the number scored
    # must not turn that into a success.
    org = FakeOrg()
    org.reject["ACC-0002"] = "Tier: bad value for restricted picklist field: B"
    org.count_script = [0, 3]
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2), make_account(3)])
    assert "ACC-0002" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (3, None, None)
    # Salesforce refused, so the advice to simply run again does not belong here.
    assert "3 account(s) carry this run's time" in str(caught.value)
    assert "run it again" not in str(caught.value).lower()


@pytest.mark.parametrize(
    "reply",
    [
        {"compositeResponse": [None]},
        {"compositeResponse": [{"httpStatusCode": 200, "body": [None, None]}]},
        {"compositeResponse": [{"httpStatusCode": 200, "body": [
            {"success": True, "errors": []}, {"success": False, "errors": [None]}]}]},
    ],
)
def test_write_reply_with_empty_pieces_fails_cleanly_and_still_reads_back(reply):
    org = FakeOrg()
    org.write_reply = FakeResponse(200, reply)
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, 0, 2)
    assert [kind for kind, _ in org.requests] == ["query", "count", "write", "count"]


def test_overlapping_run_is_not_vouched_for_when_its_own_write_gets_no_answer():
    # Two runs overlap in the same second. This run counts zero accounts with
    # its time, as it should. Then, before its write is handled, the OTHER run
    # saves 300 accounts (score 70) with the same time. This run's write
    # (score 50) then gets no answer and is not saved. The count afterwards is
    # 300, but those are the other run's accounts.
    org = FakeOrg()
    accounts = [make_account(number) for number in range(1, 301)]

    def other_run_saves_first():
        for account in accounts:
            org.accounts[account["id"]] = {
                "Sample_Id__c": account["id"], "Name": account["name"], "Score__c": 70, "Last_Scored__c": FROZEN,
            }

    org.before_write = other_run_saves_first
    org.time_out_on_write = "before saving"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, lower_scores(accounts), scored_at=FROZEN)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (300, None, None)
    assert "no answer" in str(caught.value).lower()
    assert stored_scores(org) == [70]  # this run's score of 50 was never saved, and the run did not claim it was
    # Both counts were made, and the one before the write was zero.
    assert [kind for kind, _ in org.requests] == ["query", "count", "write", "count"]


def test_program_ends_with_an_error_and_unknown_counts_when_the_write_gets_no_answer(tmp_path, capsys):
    org = FakeOrg()
    org.time_out_on_write = "after saving"
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code)
    printed = capsys.readouterr().out
    assert "Scored 2, written unknown, failed unknown." in printed
    assert "failed 0" not in printed


def test_write_with_no_answer_and_a_read_back_that_cannot_be_done_says_to_run_again():
    org = FakeOrg()
    org.time_out_on_write = "before saving"
    org.count_script = [0, FakeResponse(503, [{"errorCode": "SERVER_UNAVAILABLE", "message": "Try again later"}])]
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, None, None)
    message = str(caught.value)
    assert "no answer" in message.lower()
    assert "The read back also failed" in message and "SERVER_UNAVAILABLE" in message
    assert "The engine cannot tell whether anything was written." in message
    assert "Run it again; a rerun is safe." in message
    assert "test-token" not in message


def test_write_with_no_answer_and_accounts_carrying_the_time_says_to_run_again():
    org = FakeOrg()
    org.time_out_on_write = "after saving"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, None, None)
    message = str(caught.value)
    assert "2 account(s) carry this run's time, but the engine cannot prove this run wrote them." in message
    assert "Run it again; a rerun is safe." in message


def test_rejection_by_salesforce_does_not_say_to_run_again():
    # Salesforce answered and refused a record. Running again would be refused
    # again, so the message must name the record, not suggest a rerun.
    org = FakeOrg()
    org.reject["ACC-0002"] = "Tier: bad value for restricted picklist field: B"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2), make_account(3)])
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (3, 0, 3)
    message = str(caught.value)
    assert "ACC-0002" in message
    assert "run it again" not in message.lower()


def test_rejection_with_a_read_back_that_cannot_be_done_does_not_say_to_run_again():
    org = FakeOrg()
    org.reject["ACC-0002"] = "Tier: bad value for restricted picklist field: B"
    org.count_script = [0, FakeResponse(503, [{"errorCode": "SERVER_UNAVAILABLE", "message": "Try again later"}])]
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2), make_account(3)])
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (3, None, None)
    message = str(caught.value)
    assert "ACC-0002" in message and "The read back also failed" in message
    assert "run it again" not in message.lower()


def test_program_prints_the_rerun_advice_when_the_write_gets_no_answer_and_nothing_was_saved(tmp_path, capsys):
    org = FakeOrg()
    org.time_out_on_write = "before saving"
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code)
    assert "Run it again; a rerun is safe." in str(stopped.value.code)
    assert "Scored 2, written 0, failed 2." in capsys.readouterr().out


# --- Nothing the program prints may identify the org ------------------------
# Run logs on GitHub can be read by anyone once the repository is public.

LEAK = "Problem at https://marker-org.my.salesforce.com for marker.user@example.com, record 001MARK00000123AAA"


def assert_identifies_nothing(text):
    assert "mark" not in text.lower()
    for value in ["example-dev-ed", "salesforce.com", "https://", "test-client-id", "test-client-secret", "test-token"]:
        assert value not in text


def test_program_names_a_rejected_account_without_repeating_what_identifies_the_org(tmp_path, capsys):
    org = FakeOrg()
    org.reject["ACC-0002"] = LEAK
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code) and "ACC-0002" in str(stopped.value.code)
    assert_identifies_nothing(str(stopped.value.code) + capsys.readouterr().out)


def test_program_ends_an_error_nobody_expected_with_its_type_only(tmp_path, capsys):
    org = FakeOrg()

    def crash():
        raise RuntimeError(LEAK)

    org.before_write = crash
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code)
    assert "RuntimeError" in str(stopped.value.code)
    printed = capsys.readouterr().out
    assert "Scored unknown, written unknown, failed unknown." in printed
    assert_identifies_nothing(str(stopped.value.code) + printed)


def test_program_hides_settings_that_salesforce_echoes_back_after_login(tmp_path, capsys):
    org = FakeOrg()
    org.reject["ACC-0002"] = "Refused for client test-client-id with secret test-client-secret"
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "ACC-0002: Refused for client" in str(stopped.value.code)
    assert_identifies_nothing(str(stopped.value.code) + capsys.readouterr().out)


def test_program_explains_an_account_it_cannot_score_and_sends_nothing(tmp_path, capsys):
    # An expected problem with the data: the engine's own words are shown.
    org = FakeOrg()
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2, employees=10)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code)
    assert "Account ACC-0002: employees is 10, which fits no segment" in str(stopped.value.code)
    assert "Nothing was scored or written." in capsys.readouterr().out
    assert org.requests == []


def test_program_treats_a_value_error_nobody_expected_like_any_other_surprise(tmp_path, capsys):
    # A ValueError from somewhere other than the data must not be mistaken for
    # a data problem: its words are not repeated and the counts are not guessed.
    org = FakeOrg()

    def crash():
        raise ValueError(LEAK)

    org.before_write = crash
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code) and "ValueError" in str(stopped.value.code)
    printed = capsys.readouterr().out
    assert "Scored unknown, written unknown, failed unknown." in printed
    assert "Nothing was scored or written." not in printed
    assert_identifies_nothing(str(stopped.value.code) + printed)


# --- Choosing the accounts file, and the file that forces a failure ----------

def test_accounts_file_is_the_sample_data_unless_another_is_named():
    from write_back import ACCOUNTS_PATH, accounts_file_from
    assert accounts_file_from([]) == ACCOUNTS_PATH
    assert str(accounts_file_from(["--accounts", "tests/fixtures/forced_failure_accounts.json"])) == (
        "tests/fixtures/forced_failure_accounts.json"
    )


def test_forced_failure_file_stops_the_run_before_anything_is_sent(capsys):
    # The file the Run button's "Force a failure" box feeds the engine: two
    # accounts with the same id. The engine's own check must refuse it.
    from write_back import PROJECT_FOLDER
    org = FakeOrg()
    with pytest.raises(SystemExit) as stopped:
        main(PROJECT_FOLDER / "tests" / "fixtures" / "forced_failure_accounts.json", environ=ENVIRONMENT, http=org)
    assert "WRITE-BACK FAILED" in str(stopped.value.code)
    assert "ACC-0001: id appears more than once" in str(stopped.value.code)
    assert "nothing was sent" in str(stopped.value.code)
    assert "Scored 2, written 0, failed 2." in capsys.readouterr().out
    assert org.requests == [] and org.accounts == {}


# 1,800 accounts: two requests, each all or nothing, with a count after each
# (SPEC.md, write-back rules 4 and 5). Every account here scores the same, so
# the ranking order is the id order: ACC-0001 to ACC-1000 travel in the first
# request and ACC-1001 to ACC-1800 in the second.

LATER_RUN = "2026-11-14T13:00:00Z"


def make_1800():
    return [make_account(number) for number in range(1, 1801)]


def write_requests(org):
    return [detail for kind, detail in org.requests if kind == "write"]


def test_1800_accounts_go_as_two_requests_with_a_count_after_each():
    org = FakeOrg()
    result, printed = run(org, make_1800(), scored_at=FROZEN)
    assert result == {"scored": 1800, "written": 1800, "failed": 0}
    assert write_requests(org) == [[200, 200, 200, 200, 200], [200, 200, 200, 200]]
    # A count before anything is sent, then one after each request.
    assert [kind for kind, _ in org.requests] == ["query", "count", "write", "count", "write", "count"]
    assert len(org.accounts) == 1800
    assert printed[-1] == "Scored 1800, written 1800, failed 0."


def test_second_request_refused_keeps_the_first_1000_and_reports_the_other_800_as_not_written():
    org = FakeOrg()
    org.reject["ACC-1500"] = "Tier: bad value for restricted picklist field: Z"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, make_1800(), scored_at=FROZEN)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (1800, 1000, 800)
    assert caught.value.counts_line() == "Scored 1800, written 1000, failed 800."
    message = str(caught.value)
    assert "ACC-1500" in message
    assert "1000" in message and "800" in message
    assert "rerun is safe" in message
    # The first request stays saved; nothing from the second one is.
    assert sorted(org.accounts) == ["ACC-{:04d}".format(number) for number in range(1, 1001)]

    # Running again, once the cause is gone, leaves all 1,800 with one time.
    org.reject.clear()
    result, _ = run(org, make_1800(), scored_at=LATER_RUN)
    assert result == {"scored": 1800, "written": 1800, "failed": 0}
    assert len(org.accounts) == 1800
    assert {fields["Last_Scored__c"] for fields in org.accounts.values()} == {LATER_RUN}


def test_first_request_refused_sends_no_second_request_and_saves_nothing():
    org = FakeOrg()
    org.reject["ACC-0500"] = "Tier: bad value for restricted picklist field: Z"
    with pytest.raises(WriteBackFailed) as caught:
        run(org, make_1800(), scored_at=FROZEN)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (1800, 0, 1800)
    assert len(write_requests(org)) == 1
    assert org.accounts == {}


def test_second_request_with_no_answer_that_was_saved_reports_written_as_unknown():
    # Rule 5: it may have been saved, but a count cannot prove the accounts
    # came from this run, so the engine says it does not know.
    org = FakeOrg()
    org.time_out_on_write = "after saving"
    org.time_out_only_on_write_number = 2
    with pytest.raises(WriteBackFailed) as caught:
        run(org, make_1800(), scored_at=FROZEN)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (1800, None, None)
    assert "no answer" in str(caught.value).lower()
    assert "Run it again; a rerun is safe." in str(caught.value)


def test_second_request_with_no_answer_and_nothing_more_saved_reports_the_first_1000():
    org = FakeOrg()
    org.time_out_on_write = "before saving"
    org.time_out_only_on_write_number = 2
    with pytest.raises(WriteBackFailed) as caught:
        run(org, make_1800(), scored_at=FROZEN)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (1800, 1000, 800)
    assert "no answer" in str(caught.value).lower()
    assert "Run it again; a rerun is safe." in str(caught.value)


def test_count_that_is_wrong_after_the_first_request_sends_no_second_request():
    org = FakeOrg()
    org.count_script = [0, 999]  # none before; then 999 after the first request, where 1,000 were sent
    with pytest.raises(WriteBackFailed) as caught:
        run(org, make_1800(), scored_at=FROZEN)
    assert "read back" in str(caught.value).lower()
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (1800, 999, 801)
    assert len(write_requests(org)) == 1


def test_one_bad_record_among_1800_caught_by_the_engines_own_check_sends_nothing():
    # Guard under test: find_problems, before any request. A repeated id is
    # valid JSON and scores fine, so nothing earlier stops it.
    org = FakeOrg()
    accounts = make_1800()
    accounts[1499] = make_account(7)  # ACC-0007 now appears twice
    with pytest.raises(WriteBackFailed) as caught:
        run(org, accounts, scored_at=FROZEN)
    assert "ACC-0007" in str(caught.value)
    assert "nothing was sent" in str(caught.value)
    assert org.requests == []


# Account ownership (SPEC.md): when the engine first creates an account, an
# active Salesforce user with the same name as the account's BDR becomes its
# owner. An account that already exists is never sent an owner.

OWNER_ID = "005MARK00000123AAA"  # holds "MARK" so a leak into the output is caught


def test_new_account_whose_bdr_is_an_active_user_is_created_with_that_owner():
    org = FakeOrg()
    org.users = [{"Id": OWNER_ID, "Name": "Sample Person"}, {"Id": "005MARK00000999AAA", "Name": "Somebody Else"}]
    run(org, [make_account(1), make_account(2, bdr="No Such User")])
    assert org.accounts["ACC-0001"]["OwnerId"] == OWNER_ID
    # No user has this BDR's name, so Salesforce gives the account to the engine's own user.
    assert "OwnerId" not in org.accounts["ACC-0002"]


def test_existing_account_is_never_sent_an_owner_even_when_a_matching_user_appears_later():
    org = FakeOrg()
    run(org, [make_account(1)], scored_at=FROZEN)
    assert "OwnerId" not in org.accounts["ACC-0001"]
    org.users = [{"Id": OWNER_ID, "Name": "Sample Person"}]
    run(org, [make_account(1)], scored_at=LATER_RUN)
    assert "OwnerId" not in org.accounts["ACC-0001"]
    assert org.accounts["ACC-0001"]["Last_Scored__c"] == LATER_RUN


def test_only_the_new_accounts_in_a_mixed_run_get_an_owner():
    org = FakeOrg()
    run(org, [make_account(1)], scored_at=FROZEN)
    org.users = [{"Id": OWNER_ID, "Name": "Sample Person"}]
    run(org, [make_account(1), make_account(2)], scored_at=LATER_RUN)
    assert "OwnerId" not in org.accounts["ACC-0001"]
    assert org.accounts["ACC-0002"]["OwnerId"] == OWNER_ID


def test_two_active_users_with_one_bdrs_name_stop_the_run_before_any_write():
    org = FakeOrg()
    org.users = [{"Id": OWNER_ID, "Name": "Sample Person"}, {"Id": "005MARK00000999AAA", "Name": "Sample Person"}]
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert '"Sample Person"' in str(caught.value)
    assert "Nothing was sent" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, 0, 2)
    assert write_requests(org) == []
    assert org.accounts == {}


def test_users_are_read_after_the_count_and_only_when_an_account_is_new():
    org = FakeOrg()
    run(org, [make_account(1)], scored_at=FROZEN)
    assert [kind for kind, _ in org.requests] == ["query", "count", "users", "write", "count"]
    org.requests.clear()
    run(org, [make_account(1)], scored_at=LATER_RUN)  # nothing is new, so no owner can be set
    assert [kind for kind, _ in org.requests] == ["query", "count", "write", "count"]


def test_user_names_that_cannot_be_read_send_no_write():
    org = FakeOrg()
    org.users_reply = FakeResponse(403, [{"errorCode": "INSUFFICIENT_ACCESS", "message": LEAK}])
    with pytest.raises(WriteBackFailed) as caught:
        run(org, [make_account(1), make_account(2)])
    assert "would not list the active users" in str(caught.value)
    assert "Nothing was sent" in str(caught.value)
    assert (caught.value.scored, caught.value.written, caught.value.failed) == (2, 0, 2)
    assert write_requests(org) == []
    assert_identifies_nothing(str(caught.value))


def test_program_sets_an_owner_without_printing_who_it_is(tmp_path, capsys):
    org = FakeOrg()
    org.users = [{"Id": OWNER_ID, "Name": "Sample Person"}, {"Id": "005MARK00000999AAA", "Name": "Marker Manager"}]
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    main(path, environ=ENVIRONMENT, http=org)
    printed = capsys.readouterr().out
    assert org.accounts["ACC-0001"]["OwnerId"] == OWNER_ID
    assert "2 new account(s) get a Salesforce user as owner" in printed
    assert_identifies_nothing(printed)


def test_program_hides_an_owner_id_that_salesforce_repeats_in_a_refusal(tmp_path, capsys):
    org = FakeOrg()
    org.users = [{"Id": OWNER_ID, "Name": "Sample Person"}]
    org.reject["ACC-0002"] = "Owner ID: id value of incorrect type: " + OWNER_ID
    path = write_accounts_file(tmp_path, [make_account(1), make_account(2)])
    with pytest.raises(SystemExit) as stopped:
        main(path, environ=ENVIRONMENT, http=org)
    assert "ACC-0002" in str(stopped.value.code)
    assert_identifies_nothing(str(stopped.value.code) + capsys.readouterr().out)
