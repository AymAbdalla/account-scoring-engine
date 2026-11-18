"""Scores every sample account and writes the results into Salesforce.

The steps, in order:
  1. Score every account and check its territory (the same work run_local.py does).
     An account that fits no segment is not scored; it is written with
     Territory Status "Needs review", the reason, and empty score fields.
  2. Check every record. If any one is bad, stop: nothing is sent.
  3. Ask Salesforce which accounts it already has. New accounts get every
     field; existing ones get only the fields the engine owns.
  4. Count the accounts that already carry this run's time. It must be zero,
     so that the count afterwards can only be this run's work.
     When any account is new, also read the names of the active users: a
     new account whose BDR has the same name as a user is created with that
     user as its owner. An existing account's owner is never touched.
  5. Write the records. One all-or-nothing request holds at most 1,000, so
     the 1,800 sample accounts go as two requests, in ranking order: 1,000,
     then 800. Each request is all or nothing by itself.
  6. Read back after every request: count the accounts that carry this run's
     time. It must equal the number sent so far (1,000, then 1,800). The
     engine stops at the first request that fails or whose count is wrong.
  7. Print the scored, needs review, written and failed counts. Any failure ends with an
     error, so a scheduled run shows up as failed.

If Salesforce refuses the second request, the first 1,000 accounts stay saved
and the other 800 keep the last run's scores, until the run is repeated. The
engine says how many were written and how many were not.

It is safe to run twice: accounts are matched on their Sample Id, so a second
run updates the same 1,800 accounts and creates no duplicates.

Run from the project folder:  python write_back.py
To score another file:        python write_back.py --accounts <file>
"""
import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import requests
from dotenv import load_dotenv

from run_local import score_all
from salesforce.client import (
    MAX_RECORDS_PER_REQUEST,
    NoAnswer,
    SalesforceError,
    count_scored_at,
    find_active_users,
    find_existing_ids,
    log_in,
    read_settings,
    write_records,
)
from salesforce.records import build_record, combine, find_problems, load_field_limits
from scoring.engine import load_roster, load_rules

PROJECT_FOLDER = Path(__file__).resolve().parent
ACCOUNTS_PATH = PROJECT_FOLDER / "data" / "accounts.json"
PROBLEMS_SHOWN = 10
RUN_AGAIN = "Run it again; a rerun is safe."


class WriteBackFailed(Exception):
    """The run failed. Carries the counts to print.

    scored=None means the failure came before any account was scored.
    needs_review counts the accounts that fit no segment: not scored, but
    still written, with the flag and the reason.
    written=None means the engine could not find out how many were written.
    """

    def __init__(self, reason, scored=None, written=0, needs_review=0):
        super().__init__(reason)
        self.scored = scored
        self.needs_review = needs_review
        self.written = written
        if scored is None or written is None:
            self.failed = None
        else:
            self.failed = max(scored + needs_review - written, 0)

    def counts_line(self):
        if self.scored is None:
            return "Nothing was scored or written."
        if self.written is None:
            return "{}, written unknown, failed unknown.".format(scored_part(self.scored, self.needs_review))
        return "{}, written {}, failed {}.".format(
            scored_part(self.scored, self.needs_review), self.written, self.failed)


def scored_part(scored, needs_review):
    """"Scored 1794, needs review 6", or just "Scored 1800" when none need review."""
    if needs_review:
        return "Scored {}, needs review {}".format(scored, needs_review)
    return "Scored {}".format(scored)


def load_accounts(path):
    """Read the accounts file. Explains plainly when the file cannot be used."""
    if not Path(path).exists():
        raise WriteBackFailed("{} is missing. Create it with: python data/generate_sample.py".format(path))
    try:
        with open(path) as accounts_file:
            accounts = json.load(accounts_file)
    except ValueError:
        raise WriteBackFailed("{} is not valid JSON.".format(path))
    is_list_of_accounts = isinstance(accounts, list) and all(
        isinstance(account, dict) and account.get("id") for account in accounts
    )
    if not is_list_of_accounts:
        raise WriteBackFailed("{} must hold a list of accounts, each with an id.".format(path))
    return accounts


def write_back(accounts, rules, roster, limits, session, http=requests, say=print, scored_at=None):
    """Score, check, write and read back. Returns the counts, or raises WriteBackFailed."""
    # 1. Score. An account the engine cannot score is a problem with the data,
    #    and the engine's own explanation of it is safe to show.
    #    An account that fits no segment is not an error: it comes back with
    #    no score, flagged Needs review, and is written like any other.
    try:
        rows = combine(accounts, score_all(accounts, rules, roster, scored_at))
    except ValueError as error:
        raise WriteBackFailed(str(error))
    total = len(rows)
    needs_review = sum(1 for row in rows if row["score"] is None)
    scored = total - needs_review
    if needs_review:
        say("Scored {} accounts (rules version {}); {} fit no segment and are flagged Needs review.".format(
            scored, rules["version"], needs_review))
    else:
        say("Scored {} accounts (rules version {}).".format(scored, rules["version"]))
    counts = {"scored": scored, "needs_review": needs_review}

    # 2. Check every record before anything is sent.
    problems = find_problems(rows, limits, rules)
    if problems:
        shown = "\n  ".join(problems[:PROBLEMS_SHOWN])
        raise WriteBackFailed(
            "{} problem(s) found before writing, so nothing was sent:\n  {}".format(len(problems), shown),
            **counts,
        )
    if not rows:
        raise WriteBackFailed("There are no accounts to write.", scored=0)
    run_time = rows[0]["scored_at"]

    try:
        # 3. New or existing? Salesforce ignores upper and lower case in a
        #    Sample Id, so the comparison here does too.
        existing = {sample_id.lower() for sample_id in find_existing_ids(session, http)}

        # 4. The read back at the end counts the accounts that carry this
        #    run's time. That only proves this run's work if no account
        #    carries that time already (an earlier run in the same second would).
        already_stamped = count_scored_at(session, run_time, http)
    except SalesforceError as error:
        raise WriteBackFailed("{} Nothing was sent.".format(error), **counts)
    if already_stamped:
        raise WriteBackFailed(
            "{} account(s) already carry this run's time, {}, so a read back could not tell this run "
            "from an earlier one. Nothing was sent. Run it again.".format(already_stamped, run_time),
            **counts,
        )
    new_rows = [row for row in rows if row["id"].lower() not in existing]
    say("Salesforce already has {} of these accounts; {} are new.".format(total - len(new_rows), len(new_rows)))

    # Who owns a new account? An active Salesforce user with the same name as
    # its BDR. With no such user, the owner is left out and Salesforce gives
    # the account to the engine's own user. Only new accounts get an owner,
    # so the users are read only when something is new.
    owners = {}
    if new_rows:
        try:
            users = find_active_users(session, http)
        except SalesforceError as error:
            raise WriteBackFailed("{} Nothing was sent.".format(error), **counts)
        new_bdrs = sorted({row["bdr"] for row in new_rows})
        shared = [bdr for bdr in new_bdrs if len(users.get(bdr, [])) > 1]
        if shared:
            # Guessing an owner would be worse than stopping.
            raise WriteBackFailed(
                "More than one active Salesforce user is named {}, so the engine cannot tell who should own "
                "that BDR's new accounts. Nothing was sent.".format(", ".join('"{}"'.format(bdr) for bdr in shared)),
                **counts,
            )
        owners = {bdr: users[bdr][0] for bdr in new_bdrs if bdr in users}
        owned = sum(1 for row in new_rows if row["bdr"] in owners)
        say("{} new account(s) get a Salesforce user as owner; {} go to the engine's own user.".format(
            owned, len(new_rows) - owned))
    records = [
        build_record(row, exists=row["id"].lower() in existing, owner_id=owners.get(row["bdr"]))
        for row in rows
    ]

    # 5. Write, one all-or-nothing request at a time, in ranking order.
    #    6. Read back after each one, whatever happened. Salesforce saying
    #    "saved" proves the write; the count is a second check on it, never a
    #    substitute for it.
    requests_to_send = [
        records[start:start + MAX_RECORDS_PER_REQUEST] for start in range(0, len(records), MAX_RECORDS_PER_REQUEST)
    ]
    several = len(requests_to_send) > 1
    written = 0  # accounts in requests that Salesforce saved AND the count confirmed
    for number, request in enumerate(requests_to_send, start=1):
        which = "Request {} of {}".format(number, len(requests_to_send))
        write_error = None
        try:
            write_records(session, request, http)
        except SalesforceError as error:
            write_error = error

        # When the write request got no answer at all, the run can simply be
        # tried again, and the message says so. When Salesforce answered and
        # refused, running again would be refused again, so it does not.
        no_answer = isinstance(write_error, NoAnswer)
        try:
            counted = count_scored_at(session, run_time, http)
        except SalesforceError as error:
            # The engine cannot tell how many were written, and says so.
            reason = "{}. The read back also failed: {}. The engine cannot tell whether anything was written.".format(
                write_error or "The write was sent", error
            )
            if no_answer:
                reason += " " + RUN_AGAIN
            raise WriteBackFailed(reason, **counts, written=None)

        if write_error:
            # The write did not end in a clean "saved" from Salesforce, so the
            # run fails and nothing more is sent. That includes a request that
            # got no answer: it may have been saved, but a count cannot prove
            # the accounts came from THIS run (another run in the same second
            # would look the same).
            if counted == written:
                # The count has not moved, so nothing from this request was saved.
                if written == 0:
                    reason = str(write_error)
                    if no_answer:
                        reason += ". No account carries this run's time, so nothing was written. " + RUN_AGAIN
                    raise WriteBackFailed(reason, **counts, written=0)
                # An earlier request is saved and this one is not: Salesforce
                # now holds a mix of this run's scores and the last run's.
                reason = (
                    "{} failed, and nothing in it was saved. {}. The first {} account(s) were written and stay "
                    "saved; the other {} were not written, so Salesforce holds a mix of this run and the last "
                    "one until the run is repeated.".format(which, write_error, written, total - written)
                )
                reason += " " + (RUN_AGAIN if no_answer else "Once the cause is fixed, a rerun is safe.")
                raise WriteBackFailed(reason, **counts, written=written)
            reason = "{}. {} account(s) carry this run's time, but the engine cannot prove this run wrote them.".format(
                write_error, counted
            )
            if no_answer:
                reason += " " + RUN_AGAIN
            raise WriteBackFailed(reason, **counts, written=None)

        sent_so_far = written + len(request)
        if counted != sent_so_far:
            if several:
                reason = (
                    "After {} the read back found {} accounts with this run's time, but {} had been sent. "
                    "Nothing more was sent.".format(which.lower(), counted, sent_so_far)
                )
            else:
                reason = "The read back found {} accounts with this run's time, but {} were sent.".format(
                    counted, total
                )
            raise WriteBackFailed(reason, **counts, written=counted)
        written = sent_so_far
        if several:
            say("{}: {} accounts written; {} now carry this run's time.".format(which, len(request), written))

    # 7. Report.
    say("Read back: {} accounts carry this run's time, {}.".format(written, run_time))
    say("{}, written {}, failed 0.".format(scored_part(scored, needs_review), written))
    return {"scored": scored, "needs_review": needs_review, "written": written, "failed": 0}


def accounts_file_from(arguments):
    """Read the command line. With no option, the sample data is used.

    --accounts names another file of accounts. The GitHub workflow uses it to
    force a failure on purpose, with a file that holds a duplicate id.
    """
    parser = argparse.ArgumentParser(description="Score the accounts and write the results into Salesforce.")
    parser.add_argument("--accounts", type=Path, default=ACCOUNTS_PATH, help="the accounts file to score")
    return parser.parse_args(arguments).accounts


def main(accounts_path=ACCOUNTS_PATH, environ=None, http=requests):
    """Run the whole program. `environ` and `http` are only passed in by the tests."""
    if environ is None:
        load_dotenv()  # reads the .env file, if there is one, into the environment
        environ = os.environ
    try:
        accounts = load_accounts(accounts_path)
        try:
            rules = load_rules()
            roster = load_roster(rules)
        except ValueError as error:
            # A mistake in weights.yaml or the roster, explained in the engine's own words.
            raise WriteBackFailed(str(error))
        session = log_in(read_settings(environ), http)
        write_back(accounts, rules, roster, load_field_limits(), session, http)
    except WriteBackFailed as failure:
        print(failure.counts_line())
        sys.exit("WRITE-BACK FAILED: {}".format(failure))
    except SalesforceError as error:
        # A failure before any account was scored: a missing setting or a refused login.
        print("Nothing was scored or written.")
        sys.exit("WRITE-BACK FAILED: {}".format(error))
    except Exception as error:
        # An error nobody planned for. Its own words could repeat something
        # from Salesforce, and this output can end up in a public log, so only
        # its kind and where it happened are shown.
        last_step = traceback.extract_tb(error.__traceback__)[-1]
        print("Scored unknown, written unknown, failed unknown.")
        sys.exit(
            "WRITE-BACK FAILED: an unexpected error ({} at {} line {}). "
            "The engine cannot tell whether anything was written.".format(
                type(error).__name__, os.path.basename(last_step.filename), last_step.lineno
            )
        )


if __name__ == "__main__":
    main(accounts_file_from(sys.argv[1:]))
