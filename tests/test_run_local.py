"""Test for the local run: the saved scores come out in ranking order."""
from run_local import score_all
from scoring.engine import load_rules

RULES = load_rules()


def make_account(account_id, **changes):
    """An account that matches no rule. Each test changes only the fields it cares about."""
    account = {
        "id": account_id,
        "employees": 100,
        "countries": 1,
        "industry": "software",
        "hiring_surge": False,
        "new_hr_leader": False,
        "days_since_engaged": 60,
    }
    account.update(changes)
    return account


def test_scores_are_ranked_highest_first_then_by_account_id():
    # Deliberately out of order. Worked out by hand from SPEC.md:
    # ACC-0003 scores 0, ACC-0004 scores 20, ACC-0002 scores 20, ACC-0001 scores 45.
    accounts = [
        make_account("ACC-0003"),
        make_account("ACC-0004", hiring_surge=True),
        make_account("ACC-0002", hiring_surge=True),
        make_account("ACC-0001", industry="retail", hiring_surge=True),
    ]
    results = score_all(accounts, RULES, {})
    # Highest score first; the two 20-point accounts go by id.
    assert [r["id"] for r in results] == ["ACC-0001", "ACC-0002", "ACC-0004", "ACC-0003"]


def test_every_score_carries_its_territory_status_and_the_eleven_fields():
    roster = {"Sample Person": {"segment": "SMB", "region": "North America"}}
    accounts = [
        make_account("ACC-0001", hq_country="United States", bdr="Sample Person"),
        make_account("ACC-0002", hq_country="Germany", bdr="Sample Person"),
    ]
    results = score_all(accounts, RULES, roster)
    assert [r.get("territory_status") for r in results] == ["In territory", "Outside territory"]
    assert results[1].get("territory_note") == (
        "Outside territory: headquarters in Germany is EMEA; BDR covers North America."
    )
    assert set(results[0]) == {
        "id", "segment", "region", "score", "tier", "top_reasons", "unknown",
        "rules_version", "territory_status", "territory_note", "scored_at",
    }


def test_territory_never_changes_the_score_or_the_tier():
    # The same company, held by three different BDRs: one who covers it, one
    # who does not, and one who is not in the roster. Both fit rules plus a
    # hiring surge is 70 points, tier B, in all three cases.
    roster = {
        "Right Person": {"segment": "SMB", "region": "North America"},
        "Wrong Person": {"segment": "Enterprise", "region": "APAC"},
    }
    accounts = [
        make_account(account_id, hq_country="United States", bdr=bdr, countries=5, industry="retail", hiring_surge=True)
        for account_id, bdr in [("ACC-0001", "Right Person"), ("ACC-0002", "Wrong Person"), ("ACC-0003", "Nobody Listed")]
    ]
    results = score_all(accounts, RULES, roster)
    assert [r["territory_status"] for r in results] == ["In territory", "Outside territory", "Not checked"]
    assert [(r["score"], r["tier"]) for r in results] == [(70, "B"), (70, "B"), (70, "B")]
