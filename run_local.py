"""Scores every sample account on this computer and shows the results.

Prints how many accounts landed in each tier for each segment, then the top 3
accounts in each segment, then the accounts that sit outside their BDR's
territory, then any account that fits no segment and needs a person to review
it. Also saves every score to output/scores.json.

Run from the project folder:  python run_local.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scoring.engine import check_territory, load_roster, load_rules, score_account

PROJECT_FOLDER = Path(__file__).resolve().parent
ACCOUNTS_PATH = PROJECT_FOLDER / "data" / "accounts.json"
OUTPUT_PATH = PROJECT_FOLDER / "output" / "scores.json"
TOP_ACCOUNTS_SHOWN = 3


def score_all(accounts, rules, roster, scored_at=None):
    """Score every account, check its territory, and return the results in ranking order.

    One timestamp is shared by the whole run. It is the current UTC time,
    unless a caller hands one in (the tests do, to hold the clock still).
    """
    if scored_at is None:
        scored_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    for account in accounts:
        result = {"id": account["id"]}
        result.update(score_account(account, rules))
        result.update(check_territory(account, result, roster, rules))
        result["scored_at"] = scored_at
        results.append(result)
    # Ranking order: highest score first. Equal scores go by account id,
    # so the order is the same on every run. Accounts flagged for review have
    # no score; they come after every scored account, by id.
    results.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["id"]))
    return results


def print_tier_counts(results, rules):
    tier_names = [tier["name"] for tier in rules["tiers"]]
    print("Accounts in each tier, by segment")
    print("{:<18}".format("Segment") + "".join("{:>5}".format(name) for name in tier_names) + "{:>7}".format("Total"))
    grand_total = 0
    for segment in rules["segments"]:
        in_segment = [r for r in results if r["segment"] == segment["name"]]
        counts = [sum(1 for r in in_segment if r["tier"] == name) for name in tier_names]
        grand_total += len(in_segment)
        print("{:<18}".format(segment["name"]) + "".join("{:>5}".format(c) for c in counts) + "{:>7}".format(len(in_segment)))
    print("{:<18}".format("All segments") + " " * (5 * len(tier_names)) + "{:>7}".format(grand_total))
    needs_review = sum(1 for r in results if r["tier"] is None)
    if needs_review:
        print("{:<18}".format("Needs review") + " " * (5 * len(tier_names)) + "{:>7}".format(needs_review))


def print_territory(results, accounts, rules):
    """Print how many accounts are in, outside or not checked, then list the flagged ones.

    Accounts that need review have no segment, so they are left out here and
    listed by print_needs_review instead.
    """
    review = rules["territory_statuses"]["needs_review"]
    statuses = [status for status in rules["territory_statuses"].values() if status != review]
    results = [r for r in results if r["territory_status"] != review]
    accounts_by_id = {account["id"]: account for account in accounts}
    print()
    print("Territory check, by the account's own segment")
    print("{:<18}".format("Segment") + "".join("{:>19}".format(status) for status in statuses))
    for segment in rules["segments"]:
        in_segment = [r for r in results if r["segment"] == segment["name"]]
        counts = [sum(1 for r in in_segment if r["territory_status"] == status) for status in statuses]
        print("{:<18}".format(segment["name"]) + "".join("{:>19}".format(count) for count in counts))
    totals = [sum(1 for r in results if r["territory_status"] == status) for status in statuses]
    print("{:<18}".format("All segments") + "".join("{:>19}".format(total) for total in totals))

    flagged = [r for r in results if r["territory_status"] != rules["territory_statuses"]["in"]]
    # Best tier first. Inside a tier the results keep their ranking order.
    tier_order = [tier["name"] for tier in rules["tiers"]]
    flagged.sort(key=lambda r: tier_order.index(r["tier"]))
    print()
    print("Accounts to look at ({})".format(len(flagged)))
    for result in flagged:
        account = accounts_by_id[result["id"]]
        print("  {}  {}  (BDR: {}, Tier {}, Score {})".format(result["id"], account["name"], account["bdr"], result["tier"], result["score"]))
        print("    {}".format(result["territory_note"]))


def print_needs_review(results, accounts, rules):
    """List the accounts that fit no segment, so were not scored, with the reason."""
    review = rules["territory_statuses"]["needs_review"]
    flagged = [r for r in results if r["territory_status"] == review]
    if not flagged:
        return
    accounts_by_id = {account["id"]: account for account in accounts}
    print()
    print("Needs review: not scored ({})".format(len(flagged)))
    for result in flagged:
        account = accounts_by_id[result["id"]]
        print("  {}  {}  (BDR: {})".format(result["id"], account["name"], account["bdr"]))
        print("    {}".format(result["territory_note"]))


def print_top_accounts(results, accounts, rules):
    accounts_by_id = {account["id"]: account for account in accounts}
    for segment in rules["segments"]:
        # The results are already in ranking order, so the first few are the top ones.
        in_segment = [r for r in results if r["segment"] == segment["name"]]
        print()
        print("Top {} in {}".format(TOP_ACCOUNTS_SHOWN, segment["name"]))
        for result in in_segment[:TOP_ACCOUNTS_SHOWN]:
            account = accounts_by_id[result["id"]]
            region = result["region"] or "region unknown"
            print("  {}  {}  (BDR: {}, {})".format(result["id"], account["name"], account["bdr"], region))
            print("    Score {}, Tier {}: {}".format(result["score"], result["tier"], "; ".join(result["top_reasons"])))


if __name__ == "__main__":
    if not ACCOUNTS_PATH.exists():
        sys.exit("data/accounts.json is missing. Create it with: python data/generate_sample.py")

    with open(ACCOUNTS_PATH) as accounts_file:
        accounts = json.load(accounts_file)
    rules = load_rules()
    roster = load_roster(rules)
    results = score_all(accounts, rules, roster)

    print_tier_counts(results, rules)
    print_top_accounts(results, accounts, rules)
    print_territory(results, accounts, rules)
    print_needs_review(results, accounts, rules)

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, "w") as output_file:
        json.dump(results, output_file, indent=2)
        output_file.write("\n")
    print()
    print("Saved {} scores to {} (rules version {})".format(len(results), OUTPUT_PATH.relative_to(PROJECT_FOLDER), rules["version"]))
