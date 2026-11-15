# Changelog

Every release is listed here, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow [Semantic Versioning](https://semver.org/).

Two version numbers exist, and they are separate:
- **Release version** (v0.1.0, v0.2.0…): the version of the whole project, tagged on GitHub.
- **Rules version** (`version` in `weights.yaml`): changes only when the scoring rules change. Every score records the rules version that made it.

Only list an item here once it's built and checked.

## [Unreleased]

Work towards v0.1.0. Everything listed here is built, tested and checked. Still to come before v0.1.0 is released: a GitHub run at 1,800 accounts, the first scheduled run seen on GitHub and the demo video.

### Added

Scoring
- Scoring engine: five rules worth 100 points, tiers A to D, and up to three reasons per account in plain English. An account that matches no rule says "No rules matched".
- One rules file, `weights.yaml`: the segment bands, the regions and their countries, the rules, the tier cutoffs and the territory status names. Changing a weight changes no code, and every score records the rules version that made it.
- Segments by employee count: SMB, Mid-Market, Sub-Enterprise, Enterprise and Large Enterprise. Size decides who works an account and earns no points.
- Missing data scores zero and is reported as unknown, never as a negative signal.

Territories
- Region worked out from the headquarters country, using a country table in `weights.yaml`. A country the table does not know gives no region; the account is still scored.
- Territory check: every account is compared with the territory (segment and region) of the BDR who holds it and marked In territory, Outside territory or Not checked, with a one-line reason such as "205 employees is Mid-Market; BDR covers SMB". The engine only flags; it never moves an account.

Sample data and local run
- Sample data generator: 1,800 fictional accounts and a roster of 60 BDRs with 30 accounts each, each BDR covering one of 5 segments in one of 4 regions. A fixed seed makes every run produce the same files. Every 20th account (90 in all) is deliberately placed outside its BDR's territory and every 100th counting from number 50 (18 in all) has an unknown country, so the territory check has something to find. The sample was 300 accounts at first.
- Local run (`run_local.py`): tier counts and the top 3 accounts for each segment, the territory check, and `output/scores.json` in ranking order.

Salesforce
- Ten custom fields on Account and one permission set, kept as files and deployed with the Salesforce CLI. The permission set can read, create and edit accounts but not delete them.
- Login as the engine's own least-privilege integration user, through an External Client App with the client credentials flow. Secrets are read from the environment and never printed.
- Connection check (`check_salesforce.py`): logs in and confirms the engine can see and edit all ten fields. It only reads.
- Write-back (`write_back.py`): checks every record before sending anything, then writes the accounts in all-or-nothing requests of up to 1,000, matched on a fixed id, so a rerun updates the same accounts and creates no duplicates. The 1,800 sample accounts go as two requests, in ranking order, and the engine stops at the first one that fails. Name and BDR are set on the first load only. Score, Tier, Top Reasons, Last Scored, Segment, Region, Territory Status and Territory Note are written on every run.
- Read back: the engine counts the accounts carrying the run's time before writing and after every request. A run succeeds only when Salesforce confirms each write and each count matches. Every failure ends with an error and prints the scored, written and failed counts. When Salesforce refuses the second request, the engine reports the first 1,000 as written and the other 800 as not written.
- Account ownership: when the engine first creates an account, a Salesforce user with the same name as the account's BDR becomes its owner. The engine never changes the owner of an account that already exists. Two users sharing a BDR's name stop the run before anything is written.

Dashboards
- My Accounts: one dashboard shared by every BDR. It loads as whoever is looking and its reports are limited to accounts the viewer owns. Three tiles: my accounts by tier, my Tier A and B accounts sorted by score with the time each was last scored, and my accounts outside my territory with the engine's note.
- Team Account Scoring, for a manager: accounts scored, accounts outside territory, accounts by tier, by segment and by region (each split by tier), Tier A and B accounts for each BDR, and every Tier A account with the time it was last scored.
- Account Scoring app: four tabs, with My Accounts as its Home page.
- `Scoring_Viewer` permission set: read the ten scoring fields and open the Account Scoring app. It cannot create, edit or delete anything.
- Nine reports, the two dashboards, the Home page and the app are kept as files in `force-app/` and deployed with the fields. No file names a Salesforce user.

GitHub Actions
- Run button (`score-accounts` workflow): checks the connection to Salesforce, then runs the write-back. The three Salesforce secrets are handed only to those two steps.
- Forced failure: a "Force a failure" box on the Run button feeds the engine a file with a duplicate id, through a new `--accounts` option on `write_back.py`. The engine's own check refuses to write and the run ends red.
- Tests on every pull request and push to `main` (`tests` workflow), with no credentials.
- Both workflows name their actions by exact commit id, give the job read-only access to the code, and install libraries from a lock file with checksums enforced.

Tests
- 414 automated tests. The Salesforce tests run against a stand-in that answers the way the real org does, and a safety net makes any test fail at once if it tries to reach the real network.
- Tests that read the workflow files and fail if a safety rule is loosened: an action named by a tag, wider permissions, a risky trigger, an install without checksums, or a credential anywhere except the two agreed steps, however it is written.
- Tests that read the dashboard files and fail if a promise is broken: My Accounts no longer loads as the viewer or shows other people's accounts, a report counts accounts that were never scored, a file names a person, the viewer permission set grants more than reading, a number or chart tile draws a report it cannot show, or the BDR tile cuts off part of the team.

### Changed

- Tier A needs at least two of the three timing signals. The earlier wording said one; full fit plus one timing signal is 70 points, which is Tier B.
- Segment and Region are written to Salesforce on every run. They were first-load only before the engine worked them out itself.
- The roadmap in the README is grouped into v0.2, v0.3 and v0.4.
- Python 3.13, named in `.python-version`. Python 3.9 is past its end of support.
- Libraries are installed from `requirements.lock`, which lists every library with a checksum. `requests`, `pytest` and `python-dotenv` are raised to releases that carry published security fixes.
- A message built from a Salesforce reply shows only Salesforce's error code, the account's Sample Id and the field, for example `ACC-0002: INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST (Tier__c)`. Salesforce's own wording is never printed, because it is free text that can hold a person's name. An earlier version printed the wording after removing addresses, ids, login names and tokens. Whole replies are not printed. The connection check no longer prints the org's address. An error nobody planned for shows only its kind, file and line. Run logs on GitHub are public once the repository is.

### Proven in a Salesforce Developer org

- One bad record in a batch of 200 undid a second batch sent in the same request, so a write of 300 accounts is all or nothing.
- Two runs in a row left exactly 300 accounts with no duplicates. A BDR changed by hand survived a rerun.
- A duplicate id, a company too small for any segment and a wrong secret each ended the run with an error and left the stored scores unchanged.
- At 1,800 accounts: the 300 earlier accounts were replaced, and the first run created all 1,800 in two requests (1,000, then 800). A second run left exactly 1,800 accounts with no duplicates, all carrying that run's time. Each write request took under 5 seconds.
- The two BDRs who are real Salesforce users each own exactly their 30 accounts. The engine's own user owns the other 1,740. The engine's permission set did not change.
- When Salesforce refused the second request, the run failed and reported 1,000 accounts written and 800 not written. Exactly the first 1,000 in ranking order carried the new time and the other 800 kept the earlier one. Running again left all 1,800 with one time.
- One duplicated id among the 1,800 ended the run before anything was sent, and all 1,800 accounts were byte-for-byte the same before and after.
- Dashboards, logged in as each of the two real BDR users: My Accounts showed that user's own 30 accounts (tiers 8, 9 and 13 for one; 6, 10 and 14 for the other), their Tier B accounts highest score first, and their accounts outside territory with the engine's note. Every name, tier, score and note matched a direct query, and no account appeared for both.
- A Salesforce Platform licence can open the BDR dashboard, and the Account Scoring app with that dashboard as its Home page. A BDR who has opened the app lands on it at the next login.
- Every number on the team dashboard equals a direct count: tiers 87, 302, 621 and 790; every segment and tier; every region and tier, with 18 accounts that have no region; 90 outside territory; all 60 BDRs with their Tier A and B counts; and 87 Tier A accounts each carrying the Last Scored time held in the org.
- Two faults that a successful deploy did not show, found by looking at the screen and then fixed: a number tile drawing a plain list report showed an error in its place, and the BDR tile showed 32 of 60 BDRs because the chart's limit counts every coloured part of a bar.
- A check-only deploy of the whole `force-app/` folder passes against the Developer org. A brand-new org has not been tried.

### Proven on GitHub

- A run started with the Run button logged in to the Developer org from GitHub and wrote all 300 accounts. Only the Last Scored time changed; the other seven engine fields were identical before and after.
- A run with "Force a failure" ticked ended red with "nothing was sent", and the 300 accounts in the org were byte-for-byte the same before and after.
- A second normal run left exactly 300 accounts, with no duplicates.
- The three run logs hold no org address, Salesforce id, user name or token.
