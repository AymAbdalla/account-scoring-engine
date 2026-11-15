"""Tests for the GitHub workflow files in .github/workflows/.

A workflow is a file that tells GitHub what to run and when. These tests read
the files and fail if one of them loosens a safety rule: an action that is not
pinned to an exact version, permissions wider than reading the code, libraries
installed without their checksums, and so on.

They guard against accidents. They cannot stop a person who is allowed to
change both a workflow and this file.
"""
import copy
import re
from pathlib import Path

import pytest
import yaml

WORKFLOW_FOLDER = Path(__file__).resolve().parent.parent / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")  # owner/name@<40-character commit id>
INSTALL_COMMAND = "python -m pip install --require-hashes -r requirements.lock"


def read_workflow(name):
    return yaml.safe_load((WORKFLOW_FOLDER / name).read_text())


def triggers_of(workflow):
    """What starts the workflow, as a dictionary.

    The YAML reader turns the bare word `on` into the value True, so the
    triggers are found under True, not under "on".
    """
    triggers = workflow.get(True, workflow.get("on"))
    if isinstance(triggers, str):
        return {triggers: None}
    if isinstance(triggers, list):
        return {name: None for name in triggers}
    return triggers or {}


def steps_of(workflow):
    return [step for job in workflow.get("jobs", {}).values() for step in job.get("steps", [])]


def find_workflow_problems(workflow):
    """Every safety rule the workflow breaks. An empty list means none."""
    problems = []
    triggers = triggers_of(workflow)
    if not triggers:
        problems.append("no trigger found")
    if "pull_request_target" in triggers:
        problems.append("pull_request_target would hand secrets to a pull request from a stranger's copy")
    if workflow.get("permissions") != {"contents": "read"}:
        problems.append("permissions must be exactly: contents: read")

    for name, job in workflow.get("jobs", {}).items():
        if "uses" in job:
            problems.append("job {} runs another workflow".format(name))
        if "permissions" in job and job["permissions"] != {"contents": "read"}:
            problems.append("job {} widens the permissions".format(name))
        if job.get("runs-on") != "ubuntu-24.04":
            problems.append("job {} must run on ubuntu-24.04".format(name))
        if not isinstance(job.get("timeout-minutes"), int):
            problems.append("job {} has no time limit".format(name))

    steps = steps_of(workflow)
    for step in steps:
        action = step.get("uses")
        if action is not None and not PINNED_ACTION.match(action):
            problems.append("action {} is not pinned to a 40-character commit id".format(action))
        if action is not None and action.startswith("actions/checkout@"):
            if step.get("with", {}).get("persist-credentials") is not False:
                problems.append("checkout leaves the job's GitHub token behind")
        if action is not None and action.startswith("actions/setup-python@"):
            if step.get("with") != {"python-version-file": ".python-version"}:
                problems.append("Python must be read from .python-version and nowhere else")
        command = step.get("run", "")
        if "pip install" in command and command.strip() != INSTALL_COMMAND:
            problems.append("libraries must be installed with: " + INSTALL_COMMAND)
    if not any(step.get("run", "").strip() == INSTALL_COMMAND for step in steps):
        problems.append("no step installs the libraries from the lock file")
    if not any((step.get("uses") or "").startswith("actions/setup-python@") for step in steps):
        problems.append("no step sets up Python")
    return problems


def find_credential_problems(workflow, salesforce_commands=()):
    return []  # stub: lets the tests reach their assertions


# --- The real files ----------------------------------------------------------

def test_tests_workflow_exists():
    assert (WORKFLOW_FOLDER / "tests.yml").is_file()


@pytest.mark.parametrize("name", sorted(path.name for path in WORKFLOW_FOLDER.glob("*.y*ml")))
def test_every_workflow_keeps_the_safety_rules(name):
    assert find_workflow_problems(read_workflow(name)) == []


def test_tests_workflow_runs_on_pull_requests_and_pushes_to_main_only():
    assert triggers_of(read_workflow("tests.yml")) == {"pull_request": None, "push": {"branches": ["main"]}}


def test_tests_workflow_runs_pytest_and_is_given_no_secret():
    assert find_credential_problems(read_workflow("tests.yml")) == []
    commands = [step.get("run", "").strip() for step in steps_of(read_workflow("tests.yml"))]
    assert commands[-1] == "python -m pytest"


# --- The rules themselves: each one is seen failing --------------------------
# A good workflow, written here, and one deliberate fault at a time.

GOOD = yaml.safe_load("""
on:
  pull_request:
permissions:
  contents: read
jobs:
  tests:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0
        with:
          python-version-file: .python-version
      - run: python -m pip install --require-hashes -r requirements.lock
      - run: python -m pytest
""")


def broken(change):
    workflow = copy.deepcopy(GOOD)
    change(workflow)
    return find_workflow_problems(workflow)


def job(workflow):
    return workflow["jobs"]["tests"]


def test_the_good_example_breaks_no_rule():
    assert find_workflow_problems(GOOD) == []


def test_action_named_by_a_tag_is_a_problem():
    problems = broken(lambda w: job(w)["steps"][0].update(uses="actions/checkout@v7"))
    assert problems == ["action actions/checkout@v7 is not pinned to a 40-character commit id"]


@pytest.mark.parametrize("action", ["docker://alpine:3", "./local-action", "actions/cache@3d3c42e"])
def test_action_in_any_other_form_is_a_problem(action):
    problems = broken(lambda w: job(w)["steps"].append({"uses": action}))
    assert problems == ["action {} is not pinned to a 40-character commit id".format(action)]


def test_missing_permissions_line_is_a_problem():
    assert broken(lambda w: w.pop("permissions")) == ["permissions must be exactly: contents: read"]


@pytest.mark.parametrize("permissions", ["write-all", {"contents": "write"}, {"contents": "read", "actions": "write"}])
def test_wider_permissions_are_a_problem(permissions):
    assert broken(lambda w: w.update(permissions=permissions)) == ["permissions must be exactly: contents: read"]
    assert broken(lambda w: job(w).update(permissions=permissions)) == ["job tests widens the permissions"]


def test_pull_request_target_trigger_is_a_problem():
    problems = broken(lambda w: w[True].update(pull_request_target=None))
    assert problems == ["pull_request_target would hand secrets to a pull request from a stranger's copy"]


def test_workflow_with_no_trigger_is_a_problem():
    assert broken(lambda w: w.pop(True)) == ["no trigger found"]


def test_python_version_written_in_the_workflow_is_a_problem():
    problems = broken(lambda w: job(w)["steps"][1].update({"with": {"python-version": "3.13"}}))
    assert problems == ["Python must be read from .python-version and nowhere else"]


@pytest.mark.parametrize(
    "command",
    ["pip install -r requirements.txt", "python -m pip install -r requirements.lock", "pip install requests"],
)
def test_install_without_checksums_is_a_problem(command):
    problems = broken(lambda w: job(w)["steps"][2].update(run=command))
    assert problems == [
        "libraries must be installed with: " + INSTALL_COMMAND,
        "no step installs the libraries from the lock file",
    ]


@pytest.mark.parametrize("setting", [{}, {"persist-credentials": True}, {"persist-credentials": "false"}])
def test_checkout_that_keeps_the_token_is_a_problem(setting):
    problems = broken(lambda w: job(w)["steps"][0].update({"with": setting}))
    assert problems == ["checkout leaves the job's GitHub token behind"]


def test_other_system_or_no_time_limit_is_a_problem():
    assert broken(lambda w: job(w).update({"runs-on": "ubuntu-latest"})) == ["job tests must run on ubuntu-24.04"]
    assert broken(lambda w: job(w).pop("timeout-minutes")) == ["job tests has no time limit"]


def test_job_that_runs_another_workflow_is_a_problem():
    problems = broken(lambda w: job(w).update(uses="someone/repo/.github/workflows/x.yml@main"))
    assert problems == ["job tests runs another workflow"]


# --- The scheduled run and its Run button: score-accounts.yml ----------------

SECRET_NAMES = ["SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_INSTANCE_URL"]
CHECK_COMMAND = "python check_salesforce.py"
WRITE_COMMAND = 'python write_back.py --accounts "$ACCOUNTS_FILE"'
SALESFORCE_COMMANDS = (CHECK_COMMAND, WRITE_COMMAND)
# Which accounts file a run uses. A scheduled run has no tick box, so the
# tick box counts as "no" and the real sample data is used.
ACCOUNTS_FILE_CHOICE = (
    "${{ inputs.force_failure && 'tests/fixtures/forced_failure_accounts.json' || 'data/accounts.json' }}"
)


def score_workflow():
    return read_workflow("score-accounts.yml")


def test_score_workflow_exists():
    assert (WORKFLOW_FOLDER / "score-accounts.yml").is_file()


def test_score_workflow_starts_only_from_the_run_button_with_a_yes_or_no_box():
    triggers = triggers_of(score_workflow())
    assert list(triggers) == ["workflow_dispatch"]
    box = triggers["workflow_dispatch"]["inputs"]
    assert list(box) == ["force_failure"]
    assert box["force_failure"]["type"] == "boolean"
    assert box["force_failure"]["default"] is False


def test_score_workflow_runs_one_at_a_time_and_never_cancels_a_run_in_progress():
    assert score_workflow()["concurrency"] == {"group": "score-accounts", "cancel-in-progress": False}


def test_score_workflow_checks_the_connection_and_then_writes_with_the_same_script_every_time():
    commands = [step["run"].strip() for step in steps_of(score_workflow()) if "run" in step]
    assert commands == [INSTALL_COMMAND, CHECK_COMMAND, WRITE_COMMAND]


def test_secrets_are_handed_only_to_the_two_salesforce_steps():
    workflow = score_workflow()
    assert find_credential_problems(workflow, SALESFORCE_COMMANDS) == []
    # The two Salesforce steps really are given all three, under their own names.
    for step in steps_of(workflow):
        if step.get("run", "").strip() in SALESFORCE_COMMANDS:
            for name in SECRET_NAMES:
                assert step["env"][name] == "${{ secrets." + name + " }}"
    assert "env" not in workflow
    assert all("env" not in job for job in workflow["jobs"].values())


# --- Credentials in the wrong place: every way of writing one is caught ------
# GitHub accepts several spellings for the same secret, and ignores upper and
# lower case. Each spelling is put where it does not belong, in both workflows.

WAYS_TO_NAME_A_SECRET = [
    "${{ secrets.SF_CLIENT_SECRET }}",
    "${{ secrets['SF_CLIENT_SECRET'] }}",
    '${{ secrets["SF_CLIENT_SECRET"] }}',
    "${{ secrets [ 'SF_CLIENT_SECRET' ] }}",
    "${{ SECRETS.SF_CLIENT_SECRET }}",
    "${{ toJSON(secrets) }}",
    "${{ format('{0}', secrets.SF_CLIENT_SECRET) }}",
]


def install_step(workflow):
    return [step for step in steps_of(workflow) if step.get("run", "").strip() == INSTALL_COMMAND][0]


def first_job(workflow):
    return list(workflow["jobs"].values())[0]


WRONG_PLACES = {
    "the install step's env": lambda w, secret: install_step(w).update(env={"EXPOSED": secret}),
    "the install step's command": lambda w, secret: install_step(w).update(run=INSTALL_COMMAND + " # " + secret),
    "the checkout step's settings": lambda w, secret: steps_of(w)[0]["with"].update(token=secret),
    "a new step": lambda w, secret: first_job(w)["steps"].append({"run": "echo hello", "env": {"EXPOSED": secret}}),
    "the whole job": lambda w, secret: first_job(w).update(env={"EXPOSED": secret}),
    "the whole workflow": lambda w, secret: w.update(env={"EXPOSED": secret}),
}


@pytest.mark.parametrize("secret", WAYS_TO_NAME_A_SECRET)
@pytest.mark.parametrize("place", sorted(WRONG_PLACES))
@pytest.mark.parametrize("name, commands", [("tests.yml", ()), ("score-accounts.yml", "salesforce")])
def test_credential_in_the_wrong_place_is_a_problem_however_it_is_written(name, commands, place, secret):
    workflow = read_workflow(name)
    WRONG_PLACES[place](workflow, secret)
    assert find_credential_problems(workflow, SALESFORCE_COMMANDS if commands else ()) != []


def test_tests_workflow_may_not_be_given_credentials_even_on_a_salesforce_step():
    workflow = read_workflow("tests.yml")
    first_job(workflow)["steps"].append({
        "run": CHECK_COMMAND,
        "env": {name: "${{ secrets." + name + " }}" for name in SECRET_NAMES},
    })
    assert find_credential_problems(workflow) != []


@pytest.mark.parametrize(
    "change",
    [
        lambda env: env.update(SF_CLIENT_SECRET="${{ secrets['SF_CLIENT_SECRET'] }}"),  # another spelling
        lambda env: env.update(SF_CLIENT_SECRET="${{ secrets.SF_CLIENT_ID }}"),          # the wrong secret
        lambda env: env.update(OTHER="${{ secrets.SOMETHING_ELSE }}"),                   # a secret nobody agreed
        lambda env: env.update(SF_CLIENT_SECRET="x ${{ secrets.SF_CLIENT_SECRET }}"),    # mixed into other text
        lambda env: env.pop("SF_CLIENT_ID"),                                             # one of the three missing
    ],
)
def test_salesforce_step_must_be_given_exactly_the_three_secrets_in_the_one_agreed_form(change):
    workflow = score_workflow()
    write_step = [step for step in steps_of(workflow) if step.get("run", "").strip() == WRITE_COMMAND][0]
    change(write_step["env"])
    assert find_credential_problems(workflow, SALESFORCE_COMMANDS) != []


def test_tick_box_only_chooses_between_two_fixed_files_and_never_enters_a_command():
    text = (WORKFLOW_FOLDER / "score-accounts.yml").read_text()
    steps = steps_of(score_workflow())
    write_step = [step for step in steps if step.get("run", "").strip() == WRITE_COMMAND][0]
    assert write_step["env"]["ACCOUNTS_FILE"] == ACCOUNTS_FILE_CHOICE
    assert text.count("inputs.") == 1          # used once, in that choice
    assert "github.event" not in text
    assert all("${{" not in step.get("run", "") for step in steps)
    project = WORKFLOW_FOLDER.parent.parent
    assert (project / "tests/fixtures/forced_failure_accounts.json").is_file()
    assert (project / "data/accounts.json").is_file()
