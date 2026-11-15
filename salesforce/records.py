"""Checks the scores and shapes them into records Salesforce will accept.

Nothing in this file talks to Salesforce. It does three jobs:

1. Reads the Salesforce field files in force-app/ to learn the maximum length
   of each text field and the values of each picklist.
2. Checks every scored account BEFORE anything is written: the tier, segment,
   region and territory status against the lists in weights.yaml, and the text
   against the field lengths. One bad record means nothing gets written
   (write-back rule 4).
3. Builds the record for each account. A new account gets every field, and
   an owner when a Salesforce user has its BDR's name. An account that already
   exists gets only the fields the engine owns, so its Name, BDR and owner
   are never overwritten (write-back rule 2).
"""
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

FIELDS_FOLDER = (
    Path(__file__).resolve().parent.parent / "force-app" / "main" / "default" / "objects" / "Account" / "fields"
)
XML_NAMESPACE = "{http://soap.sforce.com/2006/04/metadata}"
ACCOUNT_NAME_MAX_LENGTH = 255  # Salesforce's own limit for the standard Name field
REASON_SEPARATOR = "; "
# The one time format the engine writes: a UTC time in whole seconds, ending in Z.
# Example: 2026-11-14T12:00:00Z
SCORED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def load_field_limits(folder=FIELDS_FOLDER):
    """Read every field file and return what each field allows.

    Example: {"Tier__c": {"values": ["A", "B", "C", "D"]}, "Sample_Id__c": {"length": 20}}
    """
    limits = {}
    for path in sorted(Path(folder).glob("*.field-meta.xml")):
        field = ElementTree.parse(str(path)).getroot()
        name = field.findtext(XML_NAMESPACE + "fullName")
        limit = {}
        length = field.findtext(XML_NAMESPACE + "length")
        if length is not None:
            limit["length"] = int(length)
        values = [
            value.findtext(XML_NAMESPACE + "fullName")
            for value in field.iter(XML_NAMESPACE + "value")
        ]
        if values:
            limit["values"] = values
        limits[name] = limit
    return limits


def combine(accounts, results):
    """Join each score to its account, keeping only what Salesforce needs."""
    accounts_by_id = {account["id"]: account for account in accounts}
    rows = []
    for result in results:
        account = accounts_by_id.get(result["id"], {})
        rows.append(
            {
                "id": result["id"],
                "name": account.get("name"),
                "segment": result["segment"],
                "bdr": account.get("bdr"),
                "region": result.get("region"),  # worked out by the engine
                "score": result["score"],
                "tier": result["tier"],
                "top_reasons": result["top_reasons"],
                "territory_status": result["territory_status"],
                "territory_note": result["territory_note"],
                "scored_at": result["scored_at"],
            }
        )
    return rows


def is_text(value, max_length):
    """True when the value is non-empty text that fits in the field."""
    return isinstance(value, str) and 0 < len(value) <= max_length


def is_utc_time(value):
    """True when the value is a real UTC time written exactly like 2026-11-14T12:00:00Z.

    Python's own date reader does the work: it refuses text in another shape,
    dates that do not exist (30 February) and times without the Z that marks UTC.
    """
    if not isinstance(value, str):
        return False
    try:
        moment = datetime.strptime(value, SCORED_AT_FORMAT)
    except ValueError:
        return False
    # Writing the time back out must give the same text. This catches
    # shorthand such as 2026-1-4T1:2:3Z, which the reader would accept.
    return moment.strftime(SCORED_AT_FORMAT) == value


def find_problems(rows, limits, rules):
    """Check every row. Returns a list of problems in plain English; empty means all good."""
    highest_score = sum(rule["points"] for rule in rules["rules"])
    # The allowed values come from weights.yaml. A test checks that the
    # Salesforce picklists hold exactly the same lists.
    allowed_segments = [segment["name"] for segment in rules["segments"]]
    allowed_regions = rules["regions"]
    allowed_tiers = [tier["name"] for tier in rules["tiers"]]
    allowed_statuses = list(rules["territory_statuses"].values())
    problems = []
    seen_ids = set()

    for position, row in enumerate(rows, start=1):
        account_id = row.get("id")
        if not is_text(account_id, limits["Sample_Id__c"]["length"]):
            problems.append("Record {}: id is missing or too long".format(position))
            continue

        def problem(field, reason):
            problems.append("{}: {} {}".format(account_id, field, reason))

        # Salesforce treats ids that differ only by upper or lower case as the same id.
        if account_id.lower() in seen_ids:
            problem("id", "appears more than once")
        seen_ids.add(account_id.lower())

        if not is_text(row.get("name"), ACCOUNT_NAME_MAX_LENGTH):
            problem("name", "is missing or too long")
        if not is_text(row.get("bdr"), limits["BDR__c"]["length"]):
            problem("bdr", "is missing or too long")
        if row.get("segment") not in allowed_segments:
            problem("segment", "is not one of the segments in weights.yaml")
        # No region is allowed: it means the headquarters country was unknown.
        if row.get("region") is not None and row.get("region") not in allowed_regions:
            problem("region", "is not one of the regions in weights.yaml")
        if row.get("territory_status") not in allowed_statuses:
            problem("territory_status", "is not one of the territory statuses in weights.yaml")
        # The note may be empty (the account is in territory) but must be text that fits.
        note = row.get("territory_note")
        if not isinstance(note, str) or len(note) > limits["Territory_Note__c"]["length"]:
            problem("territory_note", "is missing or too long")
        if row.get("tier") not in allowed_tiers:
            problem("tier", "is not one of the tiers in weights.yaml")

        score = row.get("score")
        # In Python, True and False count as whole numbers, so rule them out by name.
        is_whole_number = isinstance(score, int) and not isinstance(score, bool)
        if not is_whole_number or not 0 <= score <= highest_score:
            problem("score", "is not a whole number from 0 to {}".format(highest_score))

        reasons = row.get("top_reasons")
        reasons_are_text = isinstance(reasons, list) and reasons and all(is_text(r, 10 ** 6) for r in reasons)
        if not reasons_are_text or len(REASON_SEPARATOR.join(reasons)) > limits["Top_Reasons__c"]["length"]:
            problem("top_reasons", "is empty or too long")

        if not is_utc_time(row.get("scored_at")):
            problem("scored_at", "is not a UTC time like 2026-11-14T12:00:00Z")

    return problems


def build_record(row, exists, owner_id=None):
    """Shape one checked row into the record Salesforce expects.

    exists=True means the account is already in Salesforce.
    owner_id is the Salesforce user who should own a NEW account, or None to
    leave the choice to Salesforce (it then picks the engine's own user).
    """
    record = {
        "attributes": {"type": "Account"},
        "Sample_Id__c": row["id"],  # how Salesforce finds the account
    }
    if not exists:
        # First load only. On later runs these are left out completely, so
        # Salesforce leaves them as they are. (Sending a blank would wipe them.)
        record["Name"] = row["name"]
        record["BDR__c"] = row["bdr"]
        if owner_id is not None:
            # Set once, here, and never again: ownership is managed in
            # Salesforce, not by a scoring job.
            record["OwnerId"] = owner_id

    # The fields the engine owns, written on every run.
    record["Score__c"] = row["score"]
    record["Tier__c"] = row["tier"]
    record["Top_Reasons__c"] = REASON_SEPARATOR.join(row["top_reasons"])
    record["Last_Scored__c"] = row["scored_at"]
    record["Segment__c"] = row["segment"]
    if row["region"] is not None:
        # An unknown region is left out, so Salesforce keeps what it has.
        record["Region__c"] = row["region"]
    record["Territory_Status__c"] = row["territory_status"]
    # Always sent, even when empty, so an old note is cleared once the
    # account is back in territory.
    record["Territory_Note__c"] = row["territory_note"]
    return record
