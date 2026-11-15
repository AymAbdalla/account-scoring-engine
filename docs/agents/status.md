# Status

One line per workstream. Each builder edits only its own line; the checker does not edit this file.

| Workstream | Tool | Branch | Owns | Phase | Next |
| --- | --- | --- | --- | --- | --- |
| local-scoring-engine | builder chat | local-scoring-engine | .gitignore, requirements.txt, pytest.ini, weights.yaml, scoring/, tests/, data/, run_local.py | Build, round 1 fixes for slices 1 and 3 | Recheck in the check chat |
| salesforce-write-back | builder chat | salesforce-write-back | sfdx-project.json, force-app/, salesforce/, check_salesforce.py, write_back.py, tests/test_salesforce_*.py, tests/test_write_back.py, .env.example | Build, write-back slices wb-1, wb-2 and wb-4 built and hardened after a helper review; 300 sample accounts live in the scoring-dev org and verified equal to the engine's results | Independent check in a fresh chat (read-only on the org) |
| scheduled-run | builder chat | scheduled-run-foundation | .python-version, requirements.txt, requirements.lock, .github/, tests/test_workflows.py, tests/test_check_salesforce.py, tests/fixtures/, docs/plans/scheduled-run.md, and for slices sr-2 and sr-4 salesforce/client.py, check_salesforce.py, write_back.py and their tests | Build, sr-1 passed its check, sr-2 fixed (round 1) after two findings | Recheck sr-2 in the check chat, then build sr-3 and sr-4 |
