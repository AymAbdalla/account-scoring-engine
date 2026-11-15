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


# --- The real files ----------------------------------------------------------

def test_tests_workflow_exists():
    assert (WORKFLOW_FOLDER / "tests.yml").is_file()


@pytest.mark.parametrize("name", sorted(path.name for path in WORKFLOW_FOLDER.glob("*.y*ml")))
def test_every_workflow_keeps_the_safety_rules(name):
    assert find_workflow_problems(read_workflow(name)) == []


def test_tests_workflow_runs_on_pull_requests_and_pushes_to_main_only():
    assert triggers_of(read_workflow("tests.yml")) == {"pull_request": None, "push": {"branches": ["main"]}}


def test_tests_workflow_runs_pytest_and_is_given_no_secret():
    text = (WORKFLOW_FOLDER / "tests.yml").read_text()
    assert "secrets." not in text
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
