# Status

One line per workstream. Each builder edits only its own line; the checker does not edit this file.

| Workstream | Tool | Branch | Owns | Phase | Next |
| --- | --- | --- | --- | --- | --- |
| local-scoring-engine | builder chat | local-scoring-engine | .gitignore, requirements.txt, pytest.ini, weights.yaml, scoring/, tests/, data/, run_local.py | Finished: merged to main in pull request 4 | Nothing. Line updated by the scheduled-run builder with the owner's agreement (scheduled-run plan, decision 8) |
| salesforce-write-back | builder chat | salesforce-write-back | sfdx-project.json, force-app/, salesforce/, check_salesforce.py, write_back.py, tests/test_salesforce_*.py, tests/test_write_back.py, .env.example | Finished: merged to main in pull request 4 | Nothing. Line updated by the scheduled-run builder with the owner's agreement (scheduled-run plan, decision 8) |
| scheduled-run | builder chat | scheduled-run-schedule | .python-version, requirements.txt, requirements.lock, .github/, tests/test_workflows.py, tests/test_check_salesforce.py, tests/fixtures/, docs/plans/scheduled-run.md, and for this plan's slices salesforce/client.py, check_salesforce.py, write_back.py, their tests, README.md, CHANGELOG.md, SPEC.md and docs/RELEASING.md | Build, sr-1 to sr-4 merged in pull request 5; sr-5 (three real runs on GitHub), sr-6 and sr-7 built | Independent check of sr-5 to sr-7 in a fresh chat, then the owner pushes and opens pull request 2 |
