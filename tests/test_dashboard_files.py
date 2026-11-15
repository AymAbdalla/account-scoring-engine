"""Checks on the Salesforce report, dashboard and permission-set files.

These files in force-app/ describe the two dashboards (My Accounts and Team
Account Scoring), the reports behind their tiles, and the read-only permission
set a viewer needs. The tests read the files as XML. They need no network and
no Salesforce org, so they cannot prove Salesforce accepts the files; the
deploy does that. They guard the promises the spec makes:

- the BDR dashboard loads as whoever is looking and shows only their accounts;
- every report counts only accounts that have a tier;
- no file names a real person;
- a viewer can read the scoring fields and change nothing.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "force-app" / "main" / "default"
NS = "{http://soap.sforce.com/2006/04/metadata}"

# The words a report file uses for things that are not one of our ten fields.
ACCOUNT_NAME = "ACCOUNT.NAME"
ACCOUNT_OWNER = "USERS.NAME"
CREATED_DATE = "CREATED_DATE"  # every report file carries a date-range block on this
RECORD_COUNT = "RowCount"  # "how many accounts", not a field

# The one value a dashboard's running user may hold in the repository: a
# placeholder the deploy command swaps for a real username read from .env.
RUNNING_USER_PLACEHOLDER = "RUNNING_USER_SET_AT_DEPLOY"

EMAIL_SHAPE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")

MY_ACCOUNTS = "Account_Scoring/My_Accounts"


def _strip(tag):
    return tag.replace(NS, "")


def _text(element, name):
    found = element.find(NS + name)
    return None if found is None else (found.text or "")


def _load(folder, suffix):
    """Every file under force-app/<folder>/<Folder>/ as {"Folder/Name": xml root}."""
    files = {}
    for path in sorted((BASE / folder).glob("*/*" + suffix)):
        files[path.parent.name + "/" + path.name.replace(suffix, "")] = ET.parse(path).getroot()
    return files


def reports():
    return _load("reports", ".report-meta.xml")


def dashboards():
    return _load("dashboards", ".dashboard-meta.xml")


def tiles(dashboard):
    return list(dashboard.iter(NS + "dashboardComponent"))


def reports_drawn_by(dashboard):
    return [_text(tile, "report") for tile in tiles(dashboard)]


def scoring_fields():
    """The ten fields, as a report names them, read from the field files."""
    folder = BASE / "objects" / "Account" / "fields"
    return {"Account." + path.name.replace(".field-meta.xml", "") for path in folder.glob("*.field-meta.xml")}


def all_report_and_dashboard_paths():
    paths = []
    for folder in ("reports", "dashboards"):
        paths += sorted((BASE / folder).rglob("*-meta.xml"))
    return paths


def test_my_accounts_dashboard_loads_as_whoever_is_looking():
    found = dashboards()
    assert MY_ACCOUNTS in found, "the My Accounts dashboard file is missing"
    assert _text(found[MY_ACCOUNTS], "dashboardType") == "LoggedInUser"


def test_my_accounts_reports_show_only_accounts_the_viewer_owns():
    found = dashboards()
    assert MY_ACCOUNTS in found, "the My Accounts dashboard file is missing"
    drawn = reports_drawn_by(found[MY_ACCOUNTS])
    assert drawn, "the My Accounts dashboard has no tiles"
    kept = reports()
    for name in drawn:
        assert name in kept, "{} is not in the repository".format(name)
        assert _text(kept[name], "scope") == "user", "{} is not limited to the viewer's own accounts".format(name)


def _keeps_only_accounts_with_a_tier(report):
    for item in report.iter(NS + "criteriaItems"):
        if _text(item, "column") != "Account.Tier__c":
            continue
        operator, value = _text(item, "operator"), (_text(item, "value") or "").strip()
        if operator == "notEqual" and value == "":
            return True  # "tier is not blank"
        if operator == "equals" and value != "":
            return True  # "tier is A or B" also leaves out accounts with no tier
    return False


def test_every_report_counts_only_accounts_that_have_a_tier():
    kept = reports()
    assert kept, "there are no report files"
    for name, report in kept.items():
        assert _keeps_only_accounts_with_a_tier(report), "{} would count accounts that were never scored".format(name)


def test_every_tile_draws_a_report_kept_in_the_repository():
    found = dashboards()
    assert found, "there are no dashboard files"
    kept = reports()
    for name, dashboard in found.items():
        assert tiles(dashboard), "{} has no tiles".format(name)
        for report in reports_drawn_by(dashboard):
            assert report in kept, "{} draws {}, which is not in the repository".format(name, report)


def _fields_used(root, tags):
    used = set()
    for element in root.iter():
        if _strip(element.tag) in tags and element.text and element.text.strip():
            used.add(element.text.strip())
    return used


def test_reports_and_tiles_use_only_the_scoring_fields():
    allowed = scoring_fields() | {ACCOUNT_NAME, ACCOUNT_OWNER, CREATED_DATE, RECORD_COUNT}
    kept, found = reports(), dashboards()
    assert kept and found, "there are no report or dashboard files"
    for name, report in kept.items():
        used = _fields_used(report, {"field", "column", "sortColumn", "dateColumn"})
        assert used <= allowed, "{} uses {}".format(name, sorted(used - allowed))
    for name, dashboard in found.items():
        used = _fields_used(dashboard, {"groupingColumn", "reportColumn", "sortColumn", "column"})
        assert used <= allowed, "{} uses {}".format(name, sorted(used - allowed))


def test_no_report_or_dashboard_file_names_a_person():
    paths = all_report_and_dashboard_paths()
    assert paths, "there are no report or dashboard files"
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        assert not EMAIL_SHAPE.search(raw), "{} holds something shaped like a username or email".format(path.name)
        for element in ET.fromstring(raw).iter():
            tag = _strip(element.tag)
            assert tag != "owner", "{} names an owner".format(path.name)
            if tag == "runningUser":
                assert (element.text or "").strip() == RUNNING_USER_PLACEHOLDER, (
                    "{} names a running user".format(path.name)
                )


def test_scoring_viewer_can_read_the_ten_fields_and_change_nothing():
    path = BASE / "permissionsets" / "Scoring_Viewer.permissionset-meta.xml"
    assert path.exists(), "the Scoring_Viewer permission set file is missing"
    root = ET.parse(path).getroot()

    # Nothing in the file except a name, a description, field access and
    # object access: no system permissions, no apps, no code, no tabs.
    assert {_strip(child.tag) for child in root} <= {
        "description",
        "fieldPermissions",
        "hasActivationRequired",
        "label",
        "objectPermissions",
    }

    readable = set()
    for item in root.iter(NS + "fieldPermissions"):
        assert _text(item, "editable") == "false", "{} can be edited".format(_text(item, "field"))
        if _text(item, "readable") == "true":
            readable.add(_text(item, "field"))
    assert readable == scoring_fields()

    for item in root.iter(NS + "objectPermissions"):
        assert _text(item, "object") == "Account"
        for power in ("allowCreate", "allowEdit", "allowDelete", "modifyAllRecords", "viewAllRecords"):
            assert _text(item, power) in (None, "false"), "Scoring_Viewer grants {} on accounts".format(power)


def _box(placed):
    left, top = int(_text(placed, "columnIndex")), int(_text(placed, "rowIndex"))
    return left, top, left + int(_text(placed, "colSpan")), top + int(_text(placed, "rowSpan"))


def test_tiles_fit_the_grid_without_overlapping():
    found = dashboards()
    assert found, "there are no dashboard files"
    for name, dashboard in found.items():
        layout = dashboard.find(NS + "dashboardGridLayout")
        assert layout is not None, "{} has no grid layout".format(name)
        columns = int(_text(layout, "numberOfColumns"))
        boxes = [_box(placed) for placed in layout.findall(NS + "dashboardGridComponents")]
        assert boxes, "{} has no tiles on its grid".format(name)
        for left, top, right, bottom in boxes:
            assert 0 <= left and right <= columns, "{} has a tile that runs past the last column".format(name)
        for i, (l1, t1, r1, b1) in enumerate(boxes):
            for l2, t2, r2, b2 in boxes[i + 1 :]:
                assert not (l1 < r2 and l2 < r1 and t1 < b2 and t2 < b1), "{} has two tiles that overlap".format(name)


# --- My Accounts: the three tiles the spec settles (SPEC.md, Dashboards) ---


def _tile(dashboard, header):
    for tile in tiles(dashboard):
        if _text(tile, "header") == header:
            return tile
    return None


def _report_columns(report):
    return [_text(column, "field") for column in report.findall(NS + "columns")]


def _table_columns(tile):
    return [_text(column, "reportColumn") for column in tile.iter(NS + "flexTableColumn")]


def _filters(report):
    return {
        (_text(item, "column"), _text(item, "operator"), _text(item, "value"))
        for item in report.iter(NS + "criteriaItems")
    }


def test_my_accounts_has_its_three_tiles():
    found = dashboards()
    assert MY_ACCOUNTS in found, "the My Accounts dashboard file is missing"
    assert [_text(tile, "header") for tile in tiles(found[MY_ACCOUNTS])] == [
        "My accounts by tier",
        "My Tier A and B accounts",
        "My accounts outside my territory",
    ]


def test_my_tier_a_and_b_table_lists_name_tier_score_and_last_scored_highest_score_first():
    wanted = [ACCOUNT_NAME, "Account.Tier__c", "Account.Score__c", "Account.Last_Scored__c"]
    tile = _tile(dashboards().get(MY_ACCOUNTS, ET.Element("none")), "My Tier A and B accounts")
    assert tile is not None, "the My Tier A and B accounts tile is missing"
    assert _text(tile, "componentType") == "FlexTable"
    assert _table_columns(tile) == wanted

    report = reports()[_text(tile, "report")]
    assert _report_columns(report) == wanted
    assert ("Account.Tier__c", "equals", "A,B") in _filters(report)
    assert (_text(report, "sortColumn"), _text(report, "sortOrder")) == ("Account.Score__c", "Desc")


def test_my_outside_territory_table_shows_the_engines_note():
    tile = _tile(dashboards().get(MY_ACCOUNTS, ET.Element("none")), "My accounts outside my territory")
    assert tile is not None, "the My accounts outside my territory tile is missing"
    assert _text(tile, "componentType") == "FlexTable"
    assert _table_columns(tile)[0] == ACCOUNT_NAME
    assert "Account.Territory_Note__c" in _table_columns(tile)

    report = reports()[_text(tile, "report")]
    assert ("Account.Territory_Status__c", "equals", "Outside territory") in _filters(report)
    assert "Account.Territory_Note__c" in _report_columns(report)
