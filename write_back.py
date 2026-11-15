"""Scores every sample account and writes the results into Salesforce.

The steps, in order:
  1. Score every account and check its territory (the same work run_local.py does).
  2. Check every record. If any one is bad, stop: nothing is sent.
  3. Ask Salesforce which accounts it already has. New accounts get every
     field; existing ones get only the fields the engine owns.
  4. Count the accounts that already carry this run's time. It must be zero,
     so that the count afterwards can only be this run's work.
  5. Write all the records in one all-or-nothing request.
  6. Read back: count the accounts that carry this run's time. It must equal
     the number scored.
  7. Print the scored, written and failed counts. Any failure ends with an
     error, so a scheduled run shows up as failed.

It is safe to run twice: accounts are matched on their Sample Id, so a second
run updates the same 300 accounts and creates no duplicates.

Run from the project folder:  python write_back.py
"""
import json
import os
import sys
import traceback
from pathlib import Path

import requests
from dotenv import load_dotenv

from run_local import score_all
from salesforce.client import (
    NoAnswer,
    SalesforceError,
    count_scored_at,
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
    written=None means the engine could not find out how many were written.
    """

    def __init__(self, reason, scored=None, written=0):
        super().__init__(reason)
        self.scored = scored
        self.written = written
        if scored is None or written is None:
            self.failed = None
        else:
            self.failed = max(scored - written, 0)

    def counts_line(self):
        if self.scored is None:
            return "Nothing was scored or written."
        if self.written is None:
            return "Scored {}, written unknown, failed unknown.".format(self.scored)
        return "Scored {}, written {}, failed {}.".format(self.scored, self.written, self.failed)


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
    try:
        rows = combine(accounts, score_all(accounts, rules, roster, scored_at))
    except ValueError as error:
        raise WriteBackFailed(str(error))
    scored = len(rows)
    say("Scored {} accounts (rules version {}).".format(scored, rules["version"]))

    # 2. Check every record before anything is sent.
    problems = find_problems(rows, limits, rules)
    if problems:
        shown = "\n  ".join(problems[:PROBLEMS_SHOWN])
        raise WriteBackFailed(
            "{} problem(s) found before writing, so nothing was sent:\n  {}".format(len(problems), shown),
            scored=scored,
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
        raise WriteBackFailed("{} Nothing was sent.".format(error), scored=scored)
    if already_stamped:
        raise WriteBackFailed(
            "{} account(s) already carry this run's time, {}, so a read back could not tell this run "
            "from an earlier one. Nothing was sent. Run it again.".format(already_stamped, run_time),
            scored=scored,
        )
    records = [build_record(row, exists=row["id"].lower() in existing) for row in rows]
    already_there = sum(1 for row in rows if row["id"].lower() in existing)
    say("Salesforce already has {} of these accounts; {} are new.".format(already_there, scored - already_there))

    # 5. Write, all or nothing.
    write_error = None
    try:
        write_records(session, records, http)
    except SalesforceError as error:
        write_error = error

    # 6. Read back, whatever happened. Salesforce saying "saved" proves the
    #    write; the count is a second check on it, never a substitute for it.
    #
    #    When the write request got no answer at all, the run can simply be
    #    tried again, and the message says so. When Salesforce answered and
    #    refused, running again would be refused again, so it does not.
    no_answer = isinstance(write_error, NoAnswer)
    try:
        written = count_scored_at(session, run_time, http)
    except SalesforceError as error:
        # The engine cannot tell how many were written, and says so.
        reason = "{}. The read back also failed: {}. The engine cannot tell whether anything was written.".format(
            write_error or "The write was sent", error
        )
        if no_answer:
            reason += " " + RUN_AGAIN
        raise WriteBackFailed(reason, scored=scored, written=None)

    if write_error:
        # The write did not end in a clean "saved" from Salesforce, so the run
        # fails. That includes a request that got no answer: it may have been
        # saved, but a count cannot prove the accounts came from THIS run
        # (another run in the same second would look the same).
        if written == 0:
            reason = str(write_error)
            if no_answer:
                reason += ". No account carries this run's time, so nothing was written. " + RUN_AGAIN
            raise WriteBackFailed(reason, scored=scored, written=0)
        reason = "{}. {} account(s) carry this run's time, but the engine cannot prove this run wrote them.".format(
            write_error, written
        )
        if no_answer:
            reason += " " + RUN_AGAIN
        raise WriteBackFailed(reason, scored=scored, written=None)
    if written != scored:
        raise WriteBackFailed(
            "The read back found {} accounts with this run's time, but {} were scored.".format(written, scored),
            scored=scored,
            written=written,
        )

    # 7. Report.
    say("Read back: {} accounts carry this run's time, {}.".format(written, run_time))
    say("Scored {}, written {}, failed 0.".format(scored, written))
    return {"scored": scored, "written": written, "failed": 0}


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
    main()
