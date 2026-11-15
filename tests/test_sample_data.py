"""Tests for the sample data: 1,800 made-up accounts and 60 BDRs.

Every number here comes from SPEC.md ("In scope for v0.1"), not from the
generator: 60 BDRs with 30 accounts each, every 20th account outside its
BDR's territory (90, in equal thirds by size, by region and by both), and
every 100th account counting from number 50 with a country the table does
not know (18).
"""
import json
from collections import Counter

from data.generate_sample import make_accounts
from run_local import score_all
from scoring.engine import load_roster, load_rules

RULES = load_rules()


def load_sample():
    with open("data/accounts.json") as accounts_file:
        return json.load(accounts_file)


def score_sample():
    return score_all(load_sample(), RULES, load_roster(RULES), "2026-11-14T12:00:00Z")


def test_sample_has_1800_accounts_numbered_in_order():
    ids = [account["id"] for account in load_sample()]
    assert len(ids) == 1800
    assert ids[0] == "ACC-0001"
    assert ids[-1] == "ACC-1800"
    assert len(set(ids)) == 1800


def test_sample_has_60_bdrs_with_30_accounts_each():
    accounts_per_bdr = Counter(account["bdr"] for account in load_sample())
    assert len(accounts_per_bdr) == 60
    assert set(accounts_per_bdr.values()) == {30}


def test_roster_has_3_bdrs_for_each_segment_in_each_region():
    with open("data/bdrs.json") as roster_file:
        roster = json.load(roster_file)
    bdrs_per_territory = Counter((bdr["segment"], bdr["region"]) for bdr in roster.values())
    assert len(roster) == 60
    assert len(bdrs_per_territory) == 20  # 5 segments x 4 regions
    assert set(bdrs_per_territory.values()) == {3}


def test_territory_check_finds_1692_in_90_outside_and_18_not_checked():
    statuses = Counter(result["territory_status"] for result in score_sample())
    assert statuses == {"In territory": 1692, "Outside territory": 90, "Not checked": 18}


def test_every_20th_account_is_the_one_outside_its_territory():
    outside = sorted(r["id"] for r in score_sample() if r["territory_status"] == "Outside territory")
    assert outside[:3] == ["ACC-0020", "ACC-0040", "ACC-0060"]
    assert outside[-1] == "ACC-1800"
    assert all(int(account_id[4:]) % 20 == 0 for account_id in outside)


def test_accounts_outside_split_evenly_by_size_by_region_and_by_both():
    # The engine's note says what differs: the employee count, the headquarters, or both.
    cases = Counter()
    for result in score_sample():
        if result["territory_status"] != "Outside territory":
            continue
        by_size = "employees is" in result["territory_note"]
        by_region = "headquarters in" in result["territory_note"]
        cases[(by_size, by_region)] += 1
    assert cases == {(True, False): 30, (False, True): 30, (True, True): 30}


def test_every_bdr_has_one_or_two_accounts_outside_the_territory():
    bdr_by_id = {account["id"]: account["bdr"] for account in load_sample()}
    outside_per_bdr = Counter(
        bdr_by_id[r["id"]] for r in score_sample() if r["territory_status"] == "Outside territory"
    )
    assert len(outside_per_bdr) == 60
    assert set(outside_per_bdr.values()) == {1, 2}


def test_every_100th_account_from_number_50_has_an_unknown_country():
    not_checked = sorted(r["id"] for r in score_sample() if r["territory_status"] == "Not checked")
    assert len(not_checked) == 18
    assert not_checked[:3] == ["ACC-0050", "ACC-0150", "ACC-0250"]
    assert not_checked[-1] == "ACC-1750"


def test_saved_files_are_exactly_what_the_generator_makes():
    # Nobody edited the data by hand, and the generator gives the same answer every time.
    accounts, roster = make_accounts()
    assert accounts == load_sample()
    with open("data/bdrs.json") as roster_file:
        assert roster == json.load(roster_file)
