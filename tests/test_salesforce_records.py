"""Tests for the step that checks the scores and shapes them for Salesforce.

Nothing here talks to Salesforce. Each expected answer is written out by hand
from SPEC.md (the Salesforce fields table and the write-back rules).
"""
import json

import pytest

from run_local import score_all
from salesforce.records import build_record, combine, find_problems, load_field_limits
from scoring.engine import load_roster, load_rules

RULES = load_rules()
LIMITS = load_field_limits()


def make_row(number=1, **changes):
    """One valid scored account. Each test changes only the fields it cares about."""
    row = {
        "id": "ACC-{:04d}".format(number),
        "name": "Sample Company {}".format(number),
        "segment": "Mid-Market",
        "bdr": "Sample Person",
        "region": "EMEA",
        "score": 70,
        "tier": "B",
        "top_reasons": ["Operates in 3 or more countries", "Hourly-workforce industry", "Hiring surge"],
        "territory_status": "In territory",
        "territory_note": "",
        "scored_at": "2026-11-14T12:00:00Z",
    }
    row.update(changes)
    return row


OUTSIDE_NOTE = "Outside territory: headquarters in Germany is EMEA; BDR covers APAC."


def test_field_files_give_the_allowed_values_and_lengths_from_the_spec():
    assert LIMITS.get("Tier__c", {}).get("values") == ["A", "B", "C", "D"]
    assert LIMITS.get("Segment__c", {}).get("values") == [
        "SMB", "Mid-Market", "Sub-Enterprise", "Enterprise", "Large Enterprise",
    ]
    assert LIMITS.get("Region__c", {}).get("values") == ["North America", "EMEA", "APAC", "LATAM"]
    assert LIMITS.get("Sample_Id__c", {}).get("length") == 20
    assert LIMITS.get("Top_Reasons__c", {}).get("length") == 1000
    assert LIMITS.get("Territory_Status__c", {}).get("values") == ["In territory", "Outside territory", "Not checked", "Needs review"]
    assert LIMITS.get("Territory_Note__c", {}).get("length") == 255


def test_salesforce_picklists_match_the_tiers_segments_and_regions_in_weights_yaml():
    # The same lists live in weights.yaml and in the Salesforce field files.
    # This test fails if someone changes one and forgets the other.
    assert LIMITS.get("Tier__c", {}).get("values") == [tier["name"] for tier in RULES["tiers"]]
    assert LIMITS.get("Segment__c", {}).get("values") == [segment["name"] for segment in RULES["segments"]]
    assert LIMITS.get("Region__c", {}).get("values") == RULES.get("regions")
    assert LIMITS.get("Territory_Status__c", {}).get("values") == list(RULES["territory_statuses"].values())


@pytest.mark.parametrize(
    "rule_change, word",
    [
        ({"regions": ["North America"]}, "region"),
        ({"segments": [{"name": "SMB", "min": 50, "max": None}]}, "segment"),
        ({"tiers": [{"name": "A", "min_score": 0}]}, "tier"),
        ({"territory_statuses": {"outside": "Outside territory"}}, "territory_status"),
    ],
)
def test_allowed_values_come_from_weights_yaml(rule_change, word):
    # The row is EMEA, Mid-Market, tier B, In territory. Take that value out of the rules
    # (not out of the Salesforce field files) and the check must report it.
    narrowed_rules = dict(RULES, regions=["North America", "EMEA", "APAC", "LATAM"])
    narrowed_rules.update(rule_change)
    problems = find_problems([make_row(1)], LIMITS, narrowed_rules)
    assert len(problems) == 1
    assert word in problems[0]


def test_combine_joins_each_score_to_its_account():
    # The region comes from the engine's result, not from the account.
    accounts = [{"id": "ACC-0001", "name": "Sample Company 1", "hq_country": "Germany", "bdr": "Sample Person", "employees": 300}]
    results = [{
        "id": "ACC-0001", "segment": "Mid-Market", "region": "EMEA", "score": 70, "tier": "B",
        "top_reasons": ["Operates in 3 or more countries", "Hourly-workforce industry", "Hiring surge"],
        "unknown": [], "rules_version": 1, "territory_status": "In territory", "territory_note": "",
        "scored_at": "2026-11-14T12:00:00Z",
    }]
    assert combine(accounts, results) == [make_row(1)]


def test_all_1800_sample_scores_pass_the_check():
    # The real sample data, scored and territory-checked, has nothing to report.
    # That includes the 18 accounts whose region is unknown.
    with open("data/accounts.json") as accounts_file:
        accounts = json.load(accounts_file)
    rows = combine(accounts, score_all(accounts, RULES, load_roster(RULES)))
    assert len(rows) == 1800
    assert find_problems(rows, LIMITS, RULES) == []


def test_a_row_with_no_region_is_allowed():
    row = make_row(1, region=None, territory_status="Not checked",
                   territory_note='Not checked: headquarters country "Iceland" is not in the country table.')
    assert find_problems([row], LIMITS, RULES) == []


def test_valid_rows_have_no_problems():
    assert find_problems([make_row(1), make_row(2)], LIMITS, RULES) == []


@pytest.mark.parametrize(
    "bad_time",
    ["not-a-UTC-time", "2026-02-30T12:00:00Z", "2026-11-14T12:00:00"],
)
def test_one_bad_time_among_300_is_found_and_named(bad_time):
    rows = [make_row(number) for number in range(1, 301)]
    rows[207]["scored_at"] = bad_time  # ACC-0208
    problems = find_problems(rows, LIMITS, RULES)
    assert len(problems) == 1
    assert "ACC-0208" in problems[0]
    assert "scored_at" in problems[0]


def test_one_bad_record_among_300_is_found_and_named():
    rows = [make_row(number) for number in range(1, 301)]
    rows[149]["tier"] = "E"  # ACC-0150 gets a tier Salesforce does not have
    problems = find_problems(rows, LIMITS, RULES)
    assert len(problems) == 1
    assert "ACC-0150" in problems[0]
    assert "tier" in problems[0]


@pytest.mark.parametrize(
    "changes, word",
    [
        ({"name": ""}, "name"),
        ({"name": None}, "name"),
        ({"segment": "Tiny"}, "segment"),
        ({"region": "Antarctica"}, "region"),
        ({"territory_status": "Maybe"}, "territory_status"),
        ({"territory_status": None}, "territory_status"),
        ({"territory_note": "x" * 256}, "territory_note"),
        ({"territory_note": None}, "territory_note"),
        ({"bdr": ""}, "bdr"),
        ({"score": 101}, "score"),
        ({"score": -1}, "score"),
        ({"score": "70"}, "score"),
        ({"score": None}, "score"),
        ({"top_reasons": []}, "top_reasons"),
        ({"top_reasons": ["x" * 1001]}, "top_reasons"),
        ({"scored_at": None}, "scored_at"),
        ({"scored_at": "not-a-UTC-time"}, "scored_at"),
        ({"scored_at": "2026-02-30T12:00:00Z"}, "scored_at"),       # 30 February does not exist
        ({"scored_at": "2026-11-14T12:00:00"}, "scored_at"),        # no Z, so no time zone
        ({"scored_at": "2026-11-14T12:00:00+02:00"}, "scored_at"),  # a time zone other than UTC
        ({"scored_at": "2026-11-14T12:00:00.500Z"}, "scored_at"),   # not whole seconds
        ({"scored_at": "2026-11-14"}, "scored_at"),                 # a date with no time
        ({"scored_at": "2026-1-4T1:2:3Z"}, "scored_at"),            # not written with two digits each
        ({"scored_at": "2026-11-14T12:00:00Z "}, "scored_at"),      # a stray space at the end
    ],
)
def test_each_kind_of_bad_value_is_reported(changes, word):
    problems = find_problems([make_row(1), make_row(2, **changes)], LIMITS, RULES)
    assert len(problems) == 1
    assert "ACC-0002" in problems[0]
    assert word in problems[0]


def test_missing_id_is_reported():
    problems = find_problems([make_row(1), make_row(2, id=None)], LIMITS, RULES)
    assert len(problems) == 1
    assert "id" in problems[0]


def test_duplicate_ids_are_reported_even_when_only_the_case_differs():
    # Salesforce treats acc-0001 and ACC-0001 as the same Sample Id.
    problems = find_problems([make_row(1), make_row(2, id="acc-0001")], LIMITS, RULES)
    assert len(problems) == 1
    assert "acc-0001" in problems[0]
    assert "more than once" in problems[0]


def test_new_account_gets_every_field():
    assert build_record(make_row(1), exists=False) == {
        "attributes": {"type": "Account"},
        "Sample_Id__c": "ACC-0001",
        "Name": "Sample Company 1",
        "BDR__c": "Sample Person",
        "Score__c": 70,
        "Tier__c": "B",
        "Top_Reasons__c": "Operates in 3 or more countries; Hourly-workforce industry; Hiring surge",
        "Last_Scored__c": "2026-11-14T12:00:00Z",
        "Segment__c": "Mid-Market",
        "Region__c": "EMEA",
        "Territory_Status__c": "In territory",
        "Territory_Note__c": "",
    }


def test_existing_account_gets_only_the_engine_fields():
    # Sample_Id__c is there to find the account. Name and BDR are left out
    # completely, so Salesforce leaves them as they are. The note is sent even
    # when it is empty, so an old note is cleared.
    assert build_record(make_row(1), exists=True) == {
        "attributes": {"type": "Account"},
        "Sample_Id__c": "ACC-0001",
        "Score__c": 70,
        "Tier__c": "B",
        "Top_Reasons__c": "Operates in 3 or more countries; Hourly-workforce industry; Hiring surge",
        "Last_Scored__c": "2026-11-14T12:00:00Z",
        "Segment__c": "Mid-Market",
        "Region__c": "EMEA",
        "Territory_Status__c": "In territory",
        "Territory_Note__c": "",
    }


def test_account_outside_its_territory_carries_the_status_and_the_note():
    record = build_record(make_row(1, territory_status="Outside territory", territory_note=OUTSIDE_NOTE), exists=True)
    assert record.get("Territory_Status__c") == "Outside territory"
    assert record.get("Territory_Note__c") == OUTSIDE_NOTE


@pytest.mark.parametrize("exists", [False, True])
def test_account_with_an_unknown_region_has_no_region_in_its_record(exists):
    # Leaving Region out means Salesforce keeps whatever it has. A blank would wipe it.
    row = make_row(1, region=None, territory_status="Not checked", territory_note="Not checked: the account has no headquarters country.")
    record = build_record(row, exists=exists)
    assert "Region__c" not in record
    assert record.get("Territory_Status__c") == "Not checked"
    assert record.get("Segment__c") == "Mid-Market"


# Account ownership (SPEC.md): the owner is set once, when the account is
# first created, and never sent for an account that already exists.

def test_new_account_with_a_matching_user_is_created_with_that_owner():
    record = build_record(make_row(1), exists=False, owner_id="005000000000001AAA")
    assert record["OwnerId"] == "005000000000001AAA"
    assert record["Name"] == "Sample Company 1"


def test_new_account_with_no_matching_user_carries_no_owner():
    assert "OwnerId" not in build_record(make_row(1), exists=False)


def test_existing_account_is_never_sent_an_owner():
    assert "OwnerId" not in build_record(make_row(1), exists=True, owner_id="005000000000001AAA")
