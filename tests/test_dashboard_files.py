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


# The one report that lists accounts the engine did NOT score, on purpose:
# the accounts that fit no segment, flagged Needs review.
NEEDS_REVIEW_REPORT = "Account_Scoring/Accounts_Needing_Review"


def test_every_report_counts_only_accounts_that_have_a_tier():
    kept = reports()
    assert kept, "there are no report files"
    for name, report in kept.items():
        if name == NEEDS_REVIEW_REPORT:
            continue  # checked below: it keeps only accounts flagged Needs review
        assert _keeps_only_accounts_with_a_tier(report), "{} would count accounts that were never scored".format(name)


def test_the_needs_review_report_keeps_only_flagged_accounts():
    report = reports().get(NEEDS_REVIEW_REPORT)
    assert report is not None, "the Accounts Needing Review report is missing"
    assert ("Account.Territory_Status__c", "equals", "Needs review") in _filters(report)


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

    # Nothing in the file except a name, a description, field access, object
    # access and which app it shows: no system permissions, no code, no tabs.
    assert {_strip(child.tag) for child in root} <= {
        "applicationVisibilities",
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


# --- Team Account Scoring: the manager dashboard (SPEC.md, Dashboards) ---

TEAM = "Account_Scoring/Team_Account_Scoring"


def _groupings(report):
    return [_text(group, "field") for group in report.findall(NS + "groupingsDown")]


def test_team_dashboard_counts_every_scored_account_whoever_owns_it():
    found = dashboards()
    assert TEAM in found, "the Team Account Scoring dashboard file is missing"
    drawn = reports_drawn_by(found[TEAM])
    assert drawn, "the Team Account Scoring dashboard has no tiles"
    kept = reports()
    for name in drawn:
        assert _text(kept[name], "scope") == "organization", "{} leaves out accounts by owner".format(name)


def test_team_dashboard_shows_the_same_numbers_to_every_viewer():
    # Only My Accounts loads as the viewer. The team dashboard must not, or a
    # BDR opening it would see a team of one, and it would use up one of the
    # few viewer dashboards an org is allowed.
    found = dashboards()
    assert TEAM in found, "the Team Account Scoring dashboard file is missing"
    assert _text(found[TEAM], "dashboardType") == "SpecifiedUser"


def test_team_dashboard_opens_with_scored_count_tier_segment_and_region():
    found = dashboards()
    assert TEAM in found, "the Team Account Scoring dashboard file is missing"
    headers = [_text(tile, "header") for tile in tiles(found[TEAM])]
    for wanted in ("Accounts scored", "Accounts by tier", "By segment, split by tier", "By region, split by tier"):
        assert wanted in headers, "the tile {!r} is missing".format(wanted)


def test_segment_and_region_tiles_are_split_by_tier():
    dashboard = dashboards().get(TEAM, ET.Element("none"))
    for header, first in (("By segment, split by tier", "Account.Segment__c"), ("By region, split by tier", "Account.Region__c")):
        tile = _tile(dashboard, header)
        assert tile is not None, "the tile {!r} is missing".format(header)
        wanted = [first, "Account.Tier__c"]
        assert [g.text for g in tile.findall(NS + "groupingColumn")] == wanted
        assert _groupings(reports()[_text(tile, "report")]) == wanted


def test_accounts_scored_number_and_tier_chart_share_one_report():
    dashboard = dashboards().get(TEAM, ET.Element("none"))
    number, chart = _tile(dashboard, "Accounts scored"), _tile(dashboard, "Accounts by tier")
    assert number is not None and chart is not None, "the scored count or the tier chart is missing"
    assert _text(number, "componentType") == "Metric"
    assert _text(number, "report") == _text(chart, "report")
    assert _groupings(reports()[_text(chart, "report")]) == ["Account.Tier__c"]


# --- The Account Scoring app: My Accounts as the landing page (plan, db-6) ---

HOME_PAGE = BASE / "flexipages" / "Account_Scoring_Home.flexipage-meta.xml"
APP = BASE / "applications" / "Account_Scoring.app-meta.xml"


def test_account_scoring_home_page_shows_the_my_accounts_dashboard():
    assert HOME_PAGE.exists(), "the Account Scoring Home page file is missing"
    page = ET.parse(HOME_PAGE).getroot()
    assert _text(page, "type") == "HomePage"

    shown = []
    for component in page.iter(NS + "componentInstance"):
        if _text(component, "componentName") != "desktopDashboards:embeddedDashboard":
            continue
        for setting in component.findall(NS + "componentInstanceProperties"):
            if _text(setting, "name") == "dashboardName":
                shown.append(_text(setting, "value"))
    # The page names a dashboard without its folder; ours is Account_Scoring/My_Accounts.
    assert shown == ["My_Accounts"]
    assert MY_ACCOUNTS in dashboards(), "the dashboard the Home page shows is not in the repository"


def test_account_scoring_app_opens_on_that_home_page():
    assert APP.exists(), "the Account Scoring app file is missing"
    app = ET.parse(APP).getroot()
    assert [tab.text for tab in app.findall(NS + "tabs")][0] == "standard-home", "Home is not the app's first tab"

    homes = [
        _text(override, "content")
        for override in app.findall(NS + "actionOverrides")
        if _text(override, "pageOrSobjectType") == "standard-home" and _text(override, "type") == "Flexipage"
    ]
    assert homes == ["Account_Scoring_Home"]
    assert HOME_PAGE.exists(), "the app's Home page is not in the repository"


def test_scoring_viewer_can_open_the_account_scoring_app_and_no_other():
    path = BASE / "permissionsets" / "Scoring_Viewer.permissionset-meta.xml"
    root = ET.parse(path).getroot()
    visible = [
        (_text(item, "application"), _text(item, "visible")) for item in root.findall(NS + "applicationVisibilities")
    ]
    assert visible == [("Account_Scoring", "true")]


def test_the_repository_changes_no_profile_and_no_standard_app():
    # The landing page is a new app of our own. Editing a profile or one of
    # Salesforce's own apps would change what other users see.
    assert not list(BASE.glob("profiles/*")), "a profile file is in the repository"
    assert not list(BASE.glob("applications/standard__*")), "a file for a standard Salesforce app is in the repository"


# --- Team Account Scoring: BDR, account list and territory (plan, db-4) ---


def test_team_dashboard_has_its_eight_tiles():
    found = dashboards()
    assert TEAM in found, "the Team Account Scoring dashboard file is missing"
    assert sorted(_text(tile, "header") for tile in tiles(found[TEAM])) == sorted(
        [
            "Accounts scored",
            "Outside territory",
            "Accounts by tier",
            "By segment, split by tier",
            "By region, split by tier",
            "Tier A and B accounts by BDR",
            "Tier A accounts",
            "Needs review",
        ]
    )


def test_needs_review_table_shows_each_flagged_account_its_bdr_and_why():
    wanted = [
        ACCOUNT_NAME,
        "Account.BDR__c",
        "Account.Region__c",
        "Account.Territory_Note__c",
        "Account.Last_Scored__c",
    ]
    tile = _tile(dashboards().get(TEAM, ET.Element("none")), "Needs review")
    assert tile is not None, "the Needs review tile is missing"
    assert _text(tile, "componentType") == "FlexTable"
    assert _table_columns(tile) == wanted
    assert _text(tile, "report") == NEEDS_REVIEW_REPORT
    assert _report_columns(reports()[NEEDS_REVIEW_REPORT]) == wanted


def test_needs_review_tile_sits_below_every_other_tile_across_the_full_width():
    # The last row: a manager reads the scores first, then what needs a person.
    layout = dashboards()[TEAM].find(NS + "dashboardGridLayout")
    placed = {
        _text(component.find(NS + "dashboardComponent"), "header"): _box(component)
        for component in layout.findall(NS + "dashboardGridComponents")
    }
    left, top, right, _ = placed.pop("Needs review")
    assert (left, right) == (0, int(_text(layout, "numberOfColumns")))
    assert top >= max(bottom for _, _, _, bottom in placed.values())


def test_by_bdr_tile_shows_every_bdrs_tier_a_and_b_accounts_most_first():
    # Open item 7: every BDR holds 30 accounts, so a plain count per BDR says
    # nothing. The tile counts Tier A and B accounts, one row per BDR.
    tile = _tile(dashboards().get(TEAM, ET.Element("none")), "Tier A and B accounts by BDR")
    assert tile is not None, "the by-BDR tile is missing"
    wanted = ["Account.BDR__c", "Account.Tier__c"]
    assert [g.text for g in tile.findall(NS + "groupingColumn")] == wanted
    assert _text(tile, "sortBy") == "RowValueDescending"
    # Salesforce counts every coloured part of a bar, not every bar: 60 BDRs
    # with a Tier A part and a Tier B part are up to 120. At 60 the tile
    # showed only 32 BDRs.
    assert int(_text(tile, "maxValuesDisplayed")) >= 120, "the tile would cut off some of the 60 BDRs"

    report = reports()[_text(tile, "report")]
    assert _groupings(report) == wanted
    assert ("Account.Tier__c", "equals", "A,B") in _filters(report)


def test_tier_a_table_shows_who_holds_each_account_and_when_it_was_last_scored():
    wanted = [
        ACCOUNT_NAME,
        "Account.BDR__c",
        "Account.Segment__c",
        "Account.Region__c",
        "Account.Score__c",
        "Account.Last_Scored__c",
    ]
    tile = _tile(dashboards().get(TEAM, ET.Element("none")), "Tier A accounts")
    assert tile is not None, "the Tier A accounts tile is missing"
    assert _table_columns(tile) == wanted

    report = reports()[_text(tile, "report")]
    assert _report_columns(report) == wanted
    assert ("Account.Tier__c", "equals", "A") in _filters(report)
    assert (_text(report, "sortColumn"), _text(report, "sortOrder")) == ("Account.Score__c", "Desc")


def test_outside_territory_number_counts_accounts_the_engine_flagged():
    tile = _tile(dashboards().get(TEAM, ET.Element("none")), "Outside territory")
    assert tile is not None, "the Outside territory tile is missing"
    assert _text(tile, "componentType") == "Metric"
    assert ("Account.Territory_Status__c", "equals", "Outside territory") in _filters(reports()[_text(tile, "report")])


def test_every_number_and_chart_tile_draws_a_grouped_report():
    # Salesforce accepts a deploy where a number or chart tile points at a
    # plain list report, then shows "This report cannot be used as the source
    # for this component" on screen. Only a table tile may draw a list.
    kept = reports()
    found = dashboards()
    assert found, "there are no dashboard files"
    for name, dashboard in found.items():
        for tile in tiles(dashboard):
            if _text(tile, "componentType") == "FlexTable":
                continue
            report = kept[_text(tile, "report")]
            assert _text(report, "format") == "Summary" and _groupings(report), (
                "{}: the tile {!r} draws a report with no groups".format(name, _text(tile, "header"))
            )
