"""Checks that the engine can log in to Salesforce and use its ten fields.

Run this before the first write-back, and any time a login problem is suspected.
It reads from Salesforce only; it changes nothing there.

Run from the project folder:  python check_salesforce.py
"""
import os
import sys

from dotenv import load_dotenv

from salesforce.client import SalesforceError, find_field_problems, log_in, read_account_fields, read_settings
from salesforce.records import load_field_limits


def main():
    load_dotenv()  # reads the .env file, if there is one, into the environment
    settings = read_settings(os.environ)
    session = log_in(settings)
    print("Logged in to {}".format(session["instance_url"]))

    # The fields the engine needs are the ones defined in force-app/.
    needed = sorted(load_field_limits())
    fields = read_account_fields(session)
    problems = find_field_problems(fields, needed)
    for name in needed:
        ok = not any(problem.startswith(name + ":") for problem in problems)
        print("  {:<22} {}".format(name, "found and editable" if ok else "PROBLEM"))
    if problems:
        raise SalesforceError("Field check failed:\n  " + "\n  ".join(problems))
    print("All {} fields are found and editable.".format(len(needed)))


if __name__ == "__main__":
    try:
        main()
    except SalesforceError as error:
        sys.exit("Salesforce check failed: {}".format(error))
