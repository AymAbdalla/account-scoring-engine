"""Checks that the engine can log in to Salesforce and use its ten fields,
and that it can set an owner on a new account and read the users' names.

Run this before the first write-back, and any time a login problem is suspected.
It reads from Salesforce only; it changes nothing there. It never prints the
org's address, because its output can end up in a public GitHub log.

Run from the project folder:  python check_salesforce.py
"""
import os
import sys
import traceback

import requests
from dotenv import load_dotenv

from salesforce.client import (
    SalesforceError,
    find_active_users,
    find_field_problems,
    find_owner_problems,
    log_in,
    read_account_fields,
    read_settings,
)
from salesforce.records import load_field_limits


def main(environ=None, http=requests):
    """Run the check. `environ` and `http` are only passed in by the tests."""
    if environ is None:
        load_dotenv()  # reads the .env file, if there is one, into the environment
        environ = os.environ
    try:
        check(read_settings(environ), http)
    except SalesforceError as error:
        sys.exit("Salesforce check failed: {}".format(error))
    except Exception as error:
        # An error nobody planned for. Its own words could repeat something
        # from Salesforce, so only its kind and where it happened are shown.
        sys.exit("Salesforce check failed: an unexpected error ({}).".format(where_it_happened(error)))


def check(settings, http):
    session = log_in(settings, http)
    print("Logged in.")

    # The fields the engine needs are the ones defined in force-app/.
    needed = sorted(load_field_limits())
    fields = read_account_fields(session, http)
    problems = find_field_problems(fields, needed)
    for name in needed:
        ok = not any(problem.startswith(name + ":") for problem in problems)
        print("  {:<22} {}".format(name, "found and editable" if ok else "PROBLEM"))
    if problems:
        raise SalesforceError("Field check failed:\n  " + "\n  ".join(problems))
    print("All {} fields are found and editable.".format(len(needed)))

    # The engine sets the owner once, when it first creates an account, to
    # the Salesforce user with the same name as the account's BDR. That needs
    # two things: the Account Owner field may be set on create, and the
    # users' names can be read. No name or id is printed.
    owner_problems = find_owner_problems(fields)
    if owner_problems:
        raise SalesforceError("Owner check failed:\n  " + "\n  ".join(owner_problems))
    print("Account Owner can be set when an account is created.")
    find_active_users(session, http)
    print("The names of the active users can be read.")


def where_it_happened(error):
    """The kind of error and the file and line it came from, without the error's own words."""
    last_step = traceback.extract_tb(error.__traceback__)[-1]
    return "{} at {} line {}".format(type(error).__name__, os.path.basename(last_step.filename), last_step.lineno)


if __name__ == "__main__":
    main()
