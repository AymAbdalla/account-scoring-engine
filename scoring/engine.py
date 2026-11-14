"""The scoring engine.

Takes one account and the rules from weights.yaml, and works out the account's
segment, region, score, tier and the reasons behind the score. It also checks
whether the account sits inside the territory of the BDR who holds it.

This file knows HOW to run a check (for example "is this number at least 3?").
It does not know WHICH fields to check, what they are worth or where the tier
cutoffs are. All of that comes from weights.yaml.
"""
import json
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).resolve().parent.parent / "weights.yaml"
ROSTER_PATH = Path(__file__).resolve().parent.parent / "data" / "bdrs.json"


def load_rules(path=RULES_PATH):
    """Read weights.yaml into a Python dictionary."""
    with open(path) as rules_file:
        return yaml.safe_load(rules_file)


def find_segment(account, rules):
    """Return the name of the segment whose size band contains this account."""
    size = account.get(rules["segment_field"])
    if size is not None:
        for segment in rules["segments"]:
            above_min = size >= segment["min"]
            below_max = segment["max"] is None or size <= segment["max"]
            if above_min and below_max:
                return segment["name"]
    raise ValueError(
        "Account {}: {} is {}, which fits no segment in weights.yaml".format(
            account.get("id"), rules["segment_field"], size
        )
    )


def find_region(account, rules):
    """Return the region the account's headquarters country belongs to.

    Returns None when the country is missing or not in the table. The engine
    does not guess a region.
    """
    country = account.get(rules["region_field"])
    for region, countries in rules["countries_by_region"].items():
        if country in countries:
            return region
    return None


def rule_matches(rule, value):
    """Run one rule's check against one value from the account."""
    check = rule["check"]
    if check == "at_least":
        return value >= rule["value"]
    if check == "at_most":
        return value <= rule["value"]
    if check == "is_true":
        return value is True
    if check == "in_list":
        return value in rule["value"]
    raise ValueError("Unknown check '{}' in weights.yaml".format(check))


def find_tier(score, rules):
    """Return the first tier (highest first) whose minimum score is reached."""
    for tier in rules["tiers"]:
        if score >= tier["min_score"]:
            return tier["name"]
    raise ValueError("Score {} fits no tier in weights.yaml".format(score))


def score_account(account, rules):
    """Score one account. Returns a dictionary with the results."""
    segment = find_segment(account, rules)

    region = find_region(account, rules)

    matched = []  # the rules this account earned points for
    unknown = []  # fields that were empty, so they could not be used
    if account.get(rules["region_field"]) is None:
        unknown.append(rules["region_field"])
    for rule in rules["rules"]:
        value = account.get(rule["field"])
        if value is None:
            # Missing data earns nothing, but we report it instead of
            # treating it as a "no".
            if rule["field"] not in unknown:
                unknown.append(rule["field"])
        elif rule_matches(rule, value):
            matched.append(rule)

    score = sum(rule["points"] for rule in matched)

    # Highest points first. Python's sort keeps equal-point rules in the
    # order they appear in weights.yaml.
    matched.sort(key=lambda rule: rule["points"], reverse=True)
    top_reasons = [rule["reason"] for rule in matched[: rules["max_reasons"]]]
    if not top_reasons:
        top_reasons = [rules["no_match_reason"]]

    return {
        "segment": segment,
        "region": region,
        "score": score,
        "tier": find_tier(score, rules),
        "top_reasons": top_reasons,
        "unknown": unknown,
        "rules_version": rules["version"],
    }


def check_roster(roster, rules):
    """Stop with a clear error if a roster entry names a segment or region
    that is not in weights.yaml. That is a setup mistake, not an account problem.
    """
    segments = [segment["name"] for segment in rules["segments"]]
    for bdr, covers in roster.items():
        if covers.get("segment") not in segments or covers.get("region") not in rules["regions"]:
            raise ValueError(
                "Roster entry for {}: segment '{}' and region '{}' must both be in weights.yaml".format(
                    bdr, covers.get("segment"), covers.get("region")
                )
            )


def load_roster(rules, path=ROSTER_PATH):
    """Read the BDR roster: which segment and region each BDR covers."""
    with open(path) as roster_file:
        roster = json.load(roster_file)
    check_roster(roster, rules)
    return roster


def check_territory(account, result, roster, rules):
    """Compare the account's own territory with its BDR's territory.

    `result` is what score_account returned for this account. Returns the
    territory status and a one-line note. The note is empty when the account
    is in territory. This only reports; it never changes who holds an account.
    """
    statuses = rules["territory_statuses"]

    def answer(status, note):
        return {"territory_status": statuses[status], "territory_note": note}

    # First, the cases where the check cannot be done. We say so instead of guessing.
    bdr = account.get(rules["bdr_field"])
    if bdr is None:
        return answer("not_checked", "Not checked: the account has no BDR.")
    if bdr not in roster:
        return answer("not_checked", 'Not checked: BDR "{}" is not in the roster.'.format(bdr))
    country = account.get(rules["region_field"])
    if country is None:
        return answer("not_checked", "Not checked: the account has no headquarters country.")
    if result["region"] is None:
        return answer(
            "not_checked",
            'Not checked: headquarters country "{}" is not in the country table.'.format(country),
        )

    # Now compare size and region, and put the evidence in the note.
    covers = roster[bdr]
    reasons = []
    if result["segment"] != covers["segment"]:
        reasons.append(
            "{:,} employees is {}; BDR covers {}.".format(
                account[rules["segment_field"]], result["segment"], covers["segment"]
            )
        )
    if result["region"] != covers["region"]:
        reasons.append(
            "headquarters in {} is {}; BDR covers {}.".format(country, result["region"], covers["region"])
        )
    if not reasons:
        return answer("in", "")

    note = "Outside territory: " + reasons[0]
    for reason in reasons[1:]:
        note += " " + reason[0].upper() + reason[1:]  # a new sentence starts with a capital
    return answer("outside", note)
