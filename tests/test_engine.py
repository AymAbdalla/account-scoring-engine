"""Tests for the scoring engine.

Each test builds one made-up account, scores it with the real weights.yaml,
and compares the result with an answer worked out by hand from SPEC.md.
"""
import pytest

from scoring.engine import check_roster, check_territory, load_rules, score_account

RULES = load_rules()


def make_account(**changes):
    """An account that matches no rule. Each test changes only the fields it cares about."""
    account = {
        "id": "ACC-TEST",
        "employees": 100,
        "hq_country": "United States",
        "bdr": "Sample Person",
        "countries": 1,
        "industry": "software",
        "hiring_surge": False,
        "new_hr_leader": False,
        "days_since_engaged": 60,
    }
    account.update(changes)
    return account


def test_account_matching_every_rule_scores_100_and_is_tier_a():
    account = make_account(
        countries=5,
        industry="retail",
        hiring_surge=True,
        new_hr_leader=True,
        days_since_engaged=3,
    )
    result = score_account(account, RULES)
    assert result["score"] == 100
    assert result["tier"] == "A"


def test_account_matching_no_rule_scores_0_and_is_tier_d():
    result = score_account(make_account(), RULES)
    assert result["score"] == 0
    assert result["tier"] == "D"
    assert result["top_reasons"] == ["No rules matched"]


def test_reasons_come_out_highest_points_first_and_ties_keep_file_order():
    # All five rules match, so all five reasons show: the two 25-point rules,
    # the 20-point rule, then the two 15-point rules in file order.
    everything = make_account(
        countries=5,
        industry="retail",
        hiring_surge=True,
        new_hr_leader=True,
        days_since_engaged=3,
    )
    assert score_account(everything, RULES)["top_reasons"] == [
        "Operates in 3 or more countries",
        "Hourly-workforce industry",
        "Hiring surge",
        "New HR leader",
        "Engaged in the last 14 days",
    ]
    # Only the timing rules match: 20 points, then the two 15-point rules.
    timing_only = make_account(hiring_surge=True, new_hr_leader=True, days_since_engaged=3)
    assert score_account(timing_only, RULES)["top_reasons"] == [
        "Hiring surge",
        "New HR leader",
        "Engaged in the last 14 days",
    ]


def test_every_matched_rule_shows_so_the_reasons_add_up_to_the_score():
    # 25 + 25 + 20 + 15 = 85. Before rules version 2 only the top three showed,
    # and their points added up to 70.
    account = make_account(countries=5, industry="retail", hiring_surge=True, new_hr_leader=True)
    result = score_account(account, RULES)
    assert result["score"] == 85
    assert result["top_reasons"] == [
        "Operates in 3 or more countries",
        "Hourly-workforce industry",
        "Hiring surge",
        "New HR leader",
    ]
    assert result["rules_version"] == 2


@pytest.mark.parametrize(
    "employees, segment",
    [
        (199, "SMB"),
        (200, "Mid-Market"),
        (699, "Mid-Market"),
        (700, "Sub-Enterprise"),
        (999, "Sub-Enterprise"),
        (1000, "Enterprise"),
        (5000, "Enterprise"),
        (5001, "Large Enterprise"),
    ],
)
def test_segment_edges_land_in_the_right_segment(employees, segment):
    result = score_account(make_account(employees=employees), RULES)
    assert result["segment"] == segment


def test_full_fit_plus_one_timing_signal_scores_70_and_is_tier_b():
    account = make_account(countries=5, industry="retail", hiring_surge=True)
    result = score_account(account, RULES)
    assert result["score"] == 70
    assert result["tier"] == "B"


def test_account_matching_two_rules_lists_two_reasons():
    account = make_account(industry="logistics", new_hr_leader=True)
    result = score_account(account, RULES)
    assert result["score"] == 40
    assert result["top_reasons"] == ["Hourly-workforce industry", "New HR leader"]


def test_empty_engagement_scores_zero_and_is_reported_as_unknown():
    account = make_account(hiring_surge=True, days_since_engaged=None)
    result = score_account(account, RULES)
    assert result["score"] == 20
    assert result["unknown"] == ["days_since_engaged"]
    # A complete account reports nothing as unknown.
    assert score_account(make_account(), RULES)["unknown"] == []


def test_engagement_counts_on_day_14_and_not_on_day_15():
    assert score_account(make_account(days_since_engaged=14), RULES)["score"] == 15
    assert score_account(make_account(days_since_engaged=15), RULES)["score"] == 0


@pytest.mark.parametrize("employees", [49, 0, None, "lots", 45.5, True])
def test_account_that_fits_no_segment_is_not_scored(employees):
    # 49 is under the smallest band (SMB starts at 50). A missing count or one
    # that is not a whole number fits no band either. The account is not
    # scored, so nothing about it can be ranked against a segment.
    result = score_account(make_account(employees=employees, countries=5, industry="retail"), RULES)
    assert result["segment"] is None
    assert result["score"] is None
    assert result["tier"] is None
    assert result["top_reasons"] == []
    # Its region still comes from its country.
    assert result["region"] == "North America"


@pytest.mark.parametrize(
    "employees, note",
    [
        (49, "Needs review: 49 employees is below the smallest segment, SMB (50 to 199)."),
        (12, "Needs review: 12 employees is below the smallest segment, SMB (50 to 199)."),
        (None, "Needs review: no employee count."),
        ("lots", "Needs review: the employee count is not a whole number."),
        (45.5, "Needs review: the employee count is not a whole number."),
    ],
)
def test_account_that_fits_no_segment_is_flagged_for_review_with_the_reason(employees, note):
    # The flag wins over every territory result, even a missing BDR.
    assert territory(employees=employees) == {"territory_status": "Needs review", "territory_note": note}
    assert territory(employees=employees, bdr=None)["territory_status"] == "Needs review"


@pytest.mark.parametrize(
    "country, region",
    [
        ("United States", "North America"),
        ("Germany", "EMEA"),
        ("Japan", "APAC"),
        ("Brazil", "LATAM"),
        ("Mexico", "LATAM"),
    ],
)
def test_headquarters_country_decides_the_region(country, region):
    result = score_account(make_account(hq_country=country), RULES)
    assert result.get("region") == region


def test_country_that_is_not_in_the_table_gives_no_region_and_still_scores():
    result = score_account(make_account(hq_country="Iceland", hiring_surge=True), RULES)
    assert "region" in result
    assert result["region"] is None
    assert result["score"] == 20


def test_missing_country_gives_no_region_and_is_reported_as_unknown():
    result = score_account(make_account(hq_country=None), RULES)
    assert "region" in result
    assert result["region"] is None
    assert result["unknown"] == ["hq_country"]


def test_every_region_has_countries_and_no_country_sits_in_two_regions():
    table = RULES.get("countries_by_region", {})
    assert list(table) == ["North America", "EMEA", "APAC", "LATAM"]
    assert list(table) == RULES["regions"]
    assert all(table.values())  # no region is left without countries
    all_countries = [country for countries in table.values() for country in countries]
    assert len(all_countries) == 14
    assert len(set(all_countries)) == 14


# The BDR on the test account covers small companies in North America.
ROSTER = {"Sample Person": {"segment": "SMB", "region": "North America"}}


def territory(**changes):
    """Score a test account, then check it against its BDR's territory."""
    account = make_account(**changes)
    return check_territory(account, score_account(account, RULES), ROSTER, RULES)


def test_account_inside_its_bdrs_territory_has_no_note():
    # 100 employees is SMB and the United States is North America.
    assert territory() == {"territory_status": "In territory", "territory_note": ""}


@pytest.mark.parametrize(
    "changes, note",
    [
        (
            {"employees": 205},
            "Outside territory: 205 employees is Mid-Market; BDR covers SMB.",
        ),
        (
            {"employees": 12000},
            "Outside territory: 12,000 employees is Large Enterprise; BDR covers SMB.",
        ),
        (
            {"hq_country": "Germany"},
            "Outside territory: headquarters in Germany is EMEA; BDR covers North America.",
        ),
        (
            {"employees": 205, "hq_country": "Germany"},
            "Outside territory: 205 employees is Mid-Market; BDR covers SMB. "
            "Headquarters in Germany is EMEA; BDR covers North America.",
        ),
    ],
)
def test_account_outside_its_bdrs_territory_says_why(changes, note):
    assert territory(**changes) == {"territory_status": "Outside territory", "territory_note": note}


@pytest.mark.parametrize(
    "changes, note",
    [
        ({"hq_country": "Iceland"}, 'Not checked: headquarters country "Iceland" is not in the country table.'),
        ({"hq_country": None}, "Not checked: the account has no headquarters country."),
        ({"bdr": "Someone Else"}, 'Not checked: BDR "Someone Else" is not in the roster.'),
        ({"bdr": None}, "Not checked: the account has no BDR."),
    ],
)
def test_territory_that_cannot_be_checked_is_reported_not_guessed(changes, note):
    assert territory(**changes) == {"territory_status": "Not checked", "territory_note": note}


def test_roster_entry_with_an_unknown_segment_or_region_stops_with_a_clear_error():
    # Guard under test: check_roster compares each entry with weights.yaml.
    with pytest.raises(ValueError, match="Pat Example"):
        check_roster({"Pat Example": {"segment": "Tiny", "region": "EMEA"}}, RULES)
    with pytest.raises(ValueError, match="Pat Example"):
        check_roster({"Pat Example": {"segment": "SMB", "region": "Antarctica"}}, RULES)
    # A correct roster passes without an error.
    check_roster(ROSTER, RULES)


def test_scoring_one_account_ignores_who_holds_it():
    # Both fit rules plus a hiring surge is 70 points, tier B, whoever the BDR is.
    # (The full run is covered in tests/test_run_local.py.)
    for bdr in ["Sample Person", "Someone Else", None]:
        result = score_account(make_account(countries=5, industry="retail", hiring_surge=True, bdr=bdr), RULES)
        assert (result["score"], result["tier"]) == (70, "B")
