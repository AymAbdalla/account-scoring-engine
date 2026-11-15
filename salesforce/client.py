"""Talks to Salesforce: logs in, reads which Account fields the engine may use,
writes the scored accounts and reads them back.

The engine logs in as its own user, with a client id and a client secret from
an External Client App in Salesforce. Nobody types a password. Salesforce
answers with a token: a temporary pass that is sent along with every later
request. A fresh token is fetched on every run and never saved.

The id, the secret and the token are never printed. Neither is anything that
identifies the org: its address, a Salesforce id or a user name. A run's output
ends up in a GitHub log, which anyone can read once the repository is public,
so every message built from a Salesforce reply passes through `clean` first.

Every function here that talks to the network takes `http` as an argument. In
normal use that is the `requests` library; in the tests it is a stand-in that
plays Salesforce.
"""
import re

import requests

from salesforce.records import is_utc_time

API_VERSION = "v68.0"
TIMEOUT_SECONDS = 30  # give up on a request that gets no answer in this time
MAX_RECORDS_PER_BATCH = 200  # Salesforce's limit for one batch of records
# Salesforce's REST guide says five batches is the most one request may hold.
# A test in this project's org on 2026-11-14 accepted six, but the engine
# stays at the documented five.
MAX_BATCHES_PER_REQUEST = 5
MAX_RECORDS_PER_REQUEST = MAX_RECORDS_PER_BATCH * MAX_BATCHES_PER_REQUEST  # 1,000: the most one all-or-nothing request holds
ROLLED_BACK = "ALL_OR_NONE_OPERATION_ROLLED_BACK"  # on a record: another record was bad
HALTED = "PROCESSING_HALTED"  # on a batch: another batch was bad
SETTING_NAMES = ["SF_INSTANCE_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET"]
MAX_MESSAGE_LENGTH = 300  # longest piece of Salesforce's own text that is repeated
REASONS_SHOWN = 3

# The settings the engine logged in with. After the login they are no longer
# passed from function to function, but a later reply from Salesforce could
# still repeat one of them, so `clean` hides these values in every message.
SETTINGS_TO_HIDE = set()

# What `clean` removes from Salesforce's text, in this order.
USER_NAME = re.compile(r"\S+@\S+")                                   # looks like an email address
WEB_ADDRESS = re.compile(r"https?://\S+")
HOST_NAME = re.compile(                                               # an address without the https
    r"\b(?:[A-Za-z0-9-]+\.)+(?:com|net|org|io|dev|app|site|cloud)\b\S*", re.IGNORECASE
)
SESSION_TOKEN = re.compile(r"00D[A-Za-z0-9]{12,15}!\S+")              # an org id, "!", then the token
SALESFORCE_ID = re.compile(                                           # 15 or 18 letters and digits, with a digit
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?(?![A-Za-z0-9])"
)


class SalesforceError(Exception):
    """A problem talking to Salesforce, explained in plain English."""


class NoAnswer(SalesforceError):
    """The request got no answer (a timeout or a lost connection).

    Kept apart from a refusal because the advice differs: after a refusal the
    data needs fixing, after no answer the run can simply be tried again.
    """


def clean(text, holding=None):
    """Make a piece of Salesforce's text safe to print in a public log.

    Removes the values the engine is holding (the settings or the session),
    then anything shaped like a user name, a web address, a token or a
    Salesforce id. Puts the text on one line, so it cannot start a line with
    "::" (GitHub reads that as a command), and cuts it to a fixed length.
    """
    text = str(text)
    held = [value for value in (holding or {}).values() if isinstance(value, str) and value]
    held += sorted(SETTINGS_TO_HIDE)
    held += [value.split("://", 1)[1] for value in held if "://" in value]  # the address without https
    for value in sorted(held, key=len, reverse=True):
        text = text.replace(value, "<hidden>")
    text = USER_NAME.sub("<user>", text)
    text = WEB_ADDRESS.sub("<address>", text)
    text = HOST_NAME.sub("<address>", text)
    text = SESSION_TOKEN.sub("<token>", text)
    text = SALESFORCE_ID.sub("<id>", text)
    text = " ".join(text.split()).replace("::", ":")
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH] + "..."
    return text


def reason_given(answer, holding=None):
    """Pick Salesforce's error codes and messages out of a refusal, cleaned.

    Salesforce refuses with a list of {errorCode, message}, or at login with
    {error, error_description}. The rest of the reply is never repeated.
    """
    reasons = []
    for item in answer if isinstance(answer, list) else [answer]:
        if not isinstance(item, dict):
            continue
        code = item.get("errorCode", item.get("error"))
        message = item.get("message", item.get("error_description"))
        if isinstance(code, str) and code:
            reasons.append(clean("{}: {}".format(code, message) if isinstance(message, str) else code, holding))
    if not reasons:
        return "Salesforce gave no reason the engine could read"
    return "; ".join(reasons[:REASONS_SHOWN])


def read_settings(environ):
    """Read the org address, client id and client secret from the environment."""
    missing = [name for name in SETTING_NAMES if not environ.get(name)]
    if missing:
        raise SalesforceError(
            "Missing setting(s): {}. Put them in the .env file (see .env.example).".format(", ".join(missing))
        )
    instance_url = environ["SF_INSTANCE_URL"].strip().rstrip("/")
    if not instance_url.startswith("https://"):
        raise SalesforceError("SF_INSTANCE_URL must start with https://")
    return {
        "instance_url": instance_url,
        "client_id": environ["SF_CLIENT_ID"].strip(),
        "client_secret": environ["SF_CLIENT_SECRET"].strip(),
    }


def read_answer(response):
    """Return the answer Salesforce sent as Python data, or None if it was not JSON."""
    try:
        return response.json()
    except ValueError:
        return None


def log_in(settings, http=requests):
    """Ask Salesforce for a token. Returns the token and the org address to use."""
    SETTINGS_TO_HIDE.update(value for value in settings.values() if isinstance(value, str) and value)
    url = settings["instance_url"] + "/services/oauth2/token"
    form = {
        "grant_type": "client_credentials",
        "client_id": settings["client_id"],
        "client_secret": settings["client_secret"],
    }
    try:
        response = http.post(url, data=form, timeout=TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as error:
        # Only the kind of error is shown: never its details, which could hold
        # the request, and never the address, which identifies the org.
        raise SalesforceError("Could not reach Salesforce to log in ({})".format(type(error).__name__))

    answer = read_answer(response)
    if not isinstance(answer, dict):
        raise SalesforceError("Salesforce login failed (HTTP {})".format(response.status_code))
    if response.status_code != 200:
        # If Salesforce echoes the address, the client id or the secret back, they are hidden.
        raise SalesforceError(
            "Salesforce refused the login (HTTP {}): {}".format(response.status_code, reason_given(answer, settings))
        )
    token = answer.get("access_token")
    if not isinstance(token, str) or not token:
        raise SalesforceError("Salesforce login gave no access token")
    # Use the org address Salesforce names, if it names a proper one.
    instance_url = answer.get("instance_url")
    if not isinstance(instance_url, str) or not instance_url.startswith("https://"):
        instance_url = settings["instance_url"]
    return {"access_token": token, "instance_url": instance_url.rstrip("/")}


def read_account_fields(session, http=requests):
    """Ask Salesforce to describe Account. Returns the fields this user can see, by name."""
    url = "{}/services/data/{}/sobjects/Account/describe".format(session["instance_url"], API_VERSION)
    headers = {"Authorization": "Bearer " + session["access_token"]}
    try:
        response = http.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as error:
        raise SalesforceError("Could not reach Salesforce to read Account's fields ({})".format(type(error).__name__))

    answer = read_answer(response)
    if response.status_code != 200 or not isinstance(answer, dict):
        raise SalesforceError(
            "Salesforce would not describe Account (HTTP {}): {}".format(
                response.status_code, reason_given(answer, session)
            )
        )
    fields = answer.get("fields")
    in_expected_shape = isinstance(fields, list) and all(
        isinstance(field, dict) and isinstance(field.get("name"), str) for field in fields
    )
    if not in_expected_shape:
        raise SalesforceError("Salesforce would not describe Account: its answer was not in the expected shape")
    return {field["name"]: field for field in fields}


def find_field_problems(fields, needed_names):
    """Check that every field the engine writes is visible and editable. Empty list means all good.

    A field the user has no permission for does not show up at all, so
    "cannot see" usually means a missing permission, not a missing field.
    """
    problems = []
    for name in needed_names:
        if name not in fields:
            problems.append("{}: the engine's user cannot see this field".format(name))
        elif not (fields[name].get("createable") and fields[name].get("updateable")):
            problems.append("{}: the engine's user can see this field but cannot edit it".format(name))
    return problems


def call_salesforce(send, doing_what):
    """Make one request. If the network fails, explain it without showing the request."""
    try:
        return send()
    except requests.exceptions.RequestException as error:
        # Only the kind of error is shown, never its details, which could hold the request.
        raise NoAnswer("Salesforce gave no answer while {} ({})".format(doing_what, type(error).__name__))


def find_existing_ids(session, http=requests):
    """Return the Sample Ids of the accounts Salesforce already has.

    Salesforce answers long lists a page at a time, so this keeps asking for
    the next page until there are none left.
    """
    headers = {"Authorization": "Bearer " + session["access_token"]}
    url = "{}/services/data/{}/query".format(session["instance_url"], API_VERSION)
    params = {"q": "SELECT Sample_Id__c FROM Account WHERE Sample_Id__c != null"}
    ids = set()
    while True:
        response = call_salesforce(
            lambda: http.get(url, headers=headers, params=params, timeout=TIMEOUT_SECONDS),
            "reading the existing accounts",
        )
        answer = read_answer(response)
        if response.status_code != 200 or not isinstance(answer, dict):
            raise SalesforceError(
                "Salesforce would not list the existing accounts (HTTP {}): {}".format(
                    response.status_code, reason_given(answer, session)
                )
            )
        wrong_shape = SalesforceError("Salesforce's list of existing accounts was not in the expected shape")
        records = answer.get("records")
        if not isinstance(records, list):
            raise wrong_shape
        for record in records:
            if not isinstance(record, dict):
                raise wrong_shape
            sample_id = record.get("Sample_Id__c")
            if sample_id is None:
                continue  # an account with no Sample Id is not one of ours
            if not isinstance(sample_id, str):
                raise wrong_shape
            ids.add(sample_id)
        # More pages to come? Salesforce gives the address of the next one.
        next_page = answer.get("nextRecordsUrl")
        if next_page is None:
            return ids
        if not isinstance(next_page, str) or not next_page.startswith("/"):
            raise wrong_shape
        url = session["instance_url"] + next_page
        params = None


def write_records(session, records, http=requests):
    """Create or update the accounts in Salesforce, in ONE all-or-nothing request.

    One request holds at most 1,000 records. A bigger run is cut into several
    requests by write_back.py, which calls this once for each.

    Salesforce takes at most 200 records in one batch, so the records are split
    into batches. All the batches travel in ONE request with "all or none"
    switched on. Tested in the org on 2026-11-14: one bad record in any batch
    makes Salesforce undo every batch in the request.

    Each record is matched on its Sample Id: an account that exists is updated,
    one that does not is created. Raises SalesforceError if anything was rejected.
    """
    if not records:
        return
    batches = [records[start:start + MAX_RECORDS_PER_BATCH] for start in range(0, len(records), MAX_RECORDS_PER_BATCH)]
    if len(batches) > MAX_BATCHES_PER_REQUEST:
        raise SalesforceError(
            "{} records is more than one all-or-nothing request can hold ({})".format(
                len(records), MAX_RECORDS_PER_REQUEST
            )
        )
    upsert_address = "/services/data/{}/composite/sobjects/Account/Sample_Id__c".format(API_VERSION)
    request = {
        "allOrNone": True,
        "compositeRequest": [
            {
                "method": "PATCH",
                "url": upsert_address,
                "referenceId": "batch{}".format(number),
                "body": {"allOrNone": True, "records": batch},
            }
            for number, batch in enumerate(batches, start=1)
        ],
    }
    url = "{}/services/data/{}/composite".format(session["instance_url"], API_VERSION)
    headers = {"Authorization": "Bearer " + session["access_token"]}
    response = call_salesforce(
        lambda: http.post(url, headers=headers, json=request, timeout=TIMEOUT_SECONDS),
        "writing the accounts",
    )

    answer = read_answer(response)
    if response.status_code != 200 or not isinstance(answer, dict) or "compositeResponse" not in answer:
        raise SalesforceError(
            "Salesforce rejected the write request (HTTP {}): {}".format(
                response.status_code, reason_given(answer, session)
            )
        )

    # A reply of "200 OK" does not mean every record was saved. Each batch has
    # its own status, and each record its own success flag, so check them all.
    # Anything that is not a clean success counts as a failure.
    parts = answer["compositeResponse"]
    anything_failed = False
    reasons = []
    if not isinstance(parts, list) or len(parts) != len(batches):
        answered = len(parts) if isinstance(parts, list) else 0
        raise SalesforceError(
            "Salesforce answered for {} of {} batch(es), so the write cannot be trusted".format(answered, len(batches))
        )
    for batch, part in zip(batches, parts):
        if not isinstance(part, dict):
            raise SalesforceError("Salesforce's answer to the write was not in the expected shape")
        results = part.get("body")
        if part.get("httpStatusCode") != 200:
            anything_failed = True
        one_result_per_record = (
            isinstance(results, list)
            and len(results) == len(batch)
            and all(isinstance(result, dict) and "success" in result for result in results)
        )
        if not one_result_per_record:
            # The batch was refused as a whole, or the reply is not in the usual shape.
            anything_failed = True
            for item in results if isinstance(results, list) else []:
                # "Processing halted" only means some OTHER batch was bad. Keep the real causes.
                if isinstance(item, dict) and item.get("errorCode") and item["errorCode"] != HALTED:
                    reasons.append(clean("{}: {}".format(item["errorCode"], item.get("message")), session))
            continue
        for record, result in zip(batch, results):
            # Only a plain yes counts. Text such as "false", or a missing value, does not.
            if result["success"] is True:
                continue
            anything_failed = True
            errors = result.get("errors")
            for error in errors if isinstance(errors, list) else []:
                # "Rolled back" only means some OTHER record was bad. Keep the real causes.
                if isinstance(error, dict) and error.get("statusCode") != ROLLED_BACK:
                    reasons.append("{}: {}".format(record["Sample_Id__c"], clean(error.get("message"), session)))
    if anything_failed:
        detail = "; ".join(reasons[:5]) if reasons else "Salesforce gave no reason"
        raise SalesforceError("Salesforce rejected the write and saved nothing. {}".format(detail))


def count_scored_at(session, scored_at, http=requests):
    """Read back: how many accounts carry exactly this run's Last Scored time?"""
    # The time is placed inside a query, so only a plain UTC time is allowed through.
    if not is_utc_time(scored_at):
        raise SalesforceError("Cannot read back: {!r} is not a UTC time like 2026-11-14T12:00:00Z".format(scored_at))
    headers = {"Authorization": "Bearer " + session["access_token"]}
    url = "{}/services/data/{}/query".format(session["instance_url"], API_VERSION)
    params = {"q": "SELECT COUNT() FROM Account WHERE Last_Scored__c = {}".format(scored_at)}
    response = call_salesforce(
        lambda: http.get(url, headers=headers, params=params, timeout=TIMEOUT_SECONDS),
        "reading back the written accounts",
    )
    answer = read_answer(response)
    if response.status_code != 200 or not isinstance(answer, dict):
        raise SalesforceError(
            "Salesforce would not count the accounts (HTTP {}): {}".format(
                response.status_code, reason_given(answer, session)
            )
        )
    total = answer.get("totalSize")
    # A whole number of zero or more. (Python counts True as a number, so rule that out by type.)
    if type(total) is not int or total < 0:
        raise SalesforceError("Salesforce would not count the accounts: its answer held no usable total")
    return total
