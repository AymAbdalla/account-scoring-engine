# Spec: Account Scoring Engine v0.1

## Purpose

Rank a BDR team's accounts on transparent rules and put the result where reps already work: Salesforce. Every score has to explain itself.

## In scope for v0.1

- 1,800 generated sample accounts and 60 BDRs. Each BDR works one segment in one region: 5 segments x 4 regions (North America, EMEA, APAC, LATAM) x 3 BDRs, with 30 accounts each. Every 20th account (90 in all) is deliberately placed outside its BDR's territory, in equal thirds by size, by region and by both, so every BDR has one or two. Every 100th account counting from number 50 (18 in all) has a headquarters country that is not in the country table.
- Segment assignment by employee count, five scoring rules, tiers and top three reasons.
- Write-back of results to Salesforce Account in a free Developer org.
- A scheduled run plus a manual Run button through GitHub Actions.
- Runs on Python 3.13, named in `.python-version`.
- Two dashboards: one shared dashboard that shows each BDR only the accounts they own, and one for a manager showing the whole team by segment, BDR, region and tier. The BDR dashboard is the Home page of an Account Scoring app.
- Two of the 60 BDRs are real Salesforce users, so the BDR dashboard can be shown working. How they come to own their accounts is under Account ownership.

## Not in v0.1 (see the README roadmap)

Email, Google Sheets, Slack, a web app, a stored run log, eligibility checks, AI-written summaries, real company data and large-scale tests (the roadmap's 50,000-account test).

Also not in v0.1, by the decisions of 2026-11-15: hiding accounts between BDRs (private account sharing), more than two real BDR logins, writing with Salesforce's Bulk API 2.0, and reading accounts out of Salesforce to score them (both on the roadmap). In v0.1 the engine scores the sample accounts from its own file. Ranking unassigned accounts and assigning them to a BDR is on the README roadmap, not in v0.1.

## Sample size and the limits of the demo org

The sample is 1,800 accounts by the owner's decision. It could not be much larger in the demo: the free Developer org stores about 2,500 account records (5 MB of data storage, about 2 KB an account). That is a limit of the demo environment. The engine's own limit is different: one all-or-nothing request holds at most 1,000 records, so 1,800 accounts go as two requests (write-back rule 4). Above about 2,000 records Salesforce recommends its Bulk API 2.0, which saves each record by itself and reports the failures; that is on the roadmap, not in v0.1.

The org has 8 spare user licences, and two BDRs are real logins. The other 58 are names on accounts. In a real org every BDR would be a user, and the BDR dashboard would not change.

The demo says why the sample is small, what the demo environment limits, and how the design scales beyond it.

## Input: one account

| Field | Type | Notes |
|---|---|---|
| id | text | Fixed ID; used to match records in Salesforce |
| name | text | Generated company name |
| hq_country | text or empty | Country of the company's headquarters; decides the region |
| bdr | text | Generated BDR name; the BDR who holds the account in the sample data. Also matched by name to a Salesforce user when the account is first created (see Account ownership) |
| employees | whole number | 50 or more; decides the segment |
| countries | whole number | How many countries the company operates in |
| industry | text | |
| hiring_surge | true / false | |
| new_hr_leader | true / false | |
| days_since_engaged | whole number or empty | Empty means unknown, not "never" |

## Segments (in `weights.yaml`)

| Segment | Employees |
|---|---|
| SMB | 50 to 199 |
| Mid-Market | 200 to 699 |
| Sub-Enterprise | 700 to 999 |
| Enterprise | 1,000 to 5,000 |
| Large Enterprise | 5,001 and up |

The engine sets the segment from `employees`. Size decides who works the account; it earns no points, so an account is ranked against its own segment.

## Regions (in `weights.yaml`)

North America, EMEA, APAC and LATAM. A BDR covers one segment in one region. Region earns no points.

The engine sets the region from `hq_country`, using the table of countries for each region in `weights.yaml`:

| Region | Countries |
|---|---|
| North America | United States, Canada |
| EMEA | United Kingdom, Germany, France, Netherlands, Israel |
| APAC | Australia, Singapore, Japan, India |
| LATAM | Brazil, Mexico, Argentina |

Mexico is in LATAM by choice; a team that treats it as North America moves one line. A country that is missing or not in the table gives no region. The engine does not guess, the account is still scored, and a missing country is listed in `unknown`.

The Salesforce Segment, Region and Tier picklists hold the same lists as `weights.yaml`, and a test fails if they drift apart.

## Territory

A territory is a segment plus a region, for example "Enterprise, EMEA". The BDR roster (`data/bdrs.json` in the sample) says which territory each BDR covers. A roster entry that names a segment or region missing from `weights.yaml` stops the run, because that is a setup mistake.

For every account the engine compares the account's own territory with the territory of the BDR who holds it, and reports one of three statuses (their names are in `weights.yaml`):

| Status | When | Note |
|---|---|---|
| In territory | Segment and region both match | Empty |
| Outside territory | Segment, region or both differ | The evidence, for example `Outside territory: 205 employees is Mid-Market; BDR covers SMB.` or `Outside territory: headquarters in Germany is EMEA; BDR covers APAC.` Both sentences appear when both differ. |
| Not checked | The country is missing or not in the table, or the account has no BDR, or the BDR is not in the roster | The reason, for example `Not checked: headquarters country "Iceland" is not in the country table.` |

The engine only flags. It never changes the BDR or the Account Owner of an account that already exists, and the score and tier do not depend on territory. An account outside its BDR's territory is ranked in the segment it truly belongs to. The one time the engine sets an owner is when it first creates an account (see Account ownership).

Limits:

- The check compares against the BDR in the sample file. A BDR changed by hand in Salesforce is not seen. Reading the live owner back belongs to the reassignment item on the roadmap.
- The BDR dashboard decides "mine" by the Account Owner in Salesforce, while the territory flag is worked out from the BDR in the sample file. The two agree for the two real BDR users, and only as long as nobody changes an owner by hand.
- An account one employee over a band edge is flagged the same as one far over. The note shows the number so a person can tell them apart.
- Real territories can also use named accounts and state or postcode carve-outs. This project uses size plus headquarters region to keep the rule readable.

## Rules (in `weights.yaml`)

| Rule | Points | Group |
|---|---|---|
| Operates in 3 or more countries | 25 | Fit |
| Hourly-workforce industry (retail, hospitality, healthcare, manufacturing, logistics) | 25 | Fit |
| Hiring surge | 20 | Timing |
| New HR leader | 15 | Timing |
| Engaged in the last 14 days | 15 | Timing |

Tiers: **A** 75 and up, **B** 55 to 74, **C** 35 to 54, **D** under 35. Fit totals 50 at most and the biggest single timing signal is 20, so Tier A always needs at least two timing signals: both fit rules plus two timing signals (80 or more), or one fit rule plus all three (75). Full fit plus one timing signal tops out at 70, which is Tier B.

No rule logic lives outside `weights.yaml`.

## Output: one scored account (in `output/scores.json`)

`id`, `segment`, `region` (empty when the country is missing or not in the table), `score`, `tier`, `top_reasons` (the rules that matched, highest points first, three at most; rules with equal points keep their order in `weights.yaml`; an account that matched fewer than three rules lists only those, and an account that matched none gets the single line "No rules matched"), `unknown` (the names of any fields that were empty; an empty field scores zero for its rule and is reported here instead of counting as a negative signal), `rules_version`, `territory_status`, `territory_note` (empty when the account is in territory), `scored_at` (UTC time).

Ranking order: score from highest to lowest, then account `id`, so the order is the same on every run.

## Salesforce fields on Account

| Field | Type | Written by |
|---|---|---|
| Sample Id | Text, External ID, unique | First load only |
| Score | Number | Engine, every run |
| Tier | Picklist (A, B, C, D) | Engine, every run |
| Top Reasons | Long text | Engine, every run |
| Last Scored | Date/Time | Engine, every run |
| Segment | Picklist (the five segments) | Engine, every run |
| Region | Picklist (the four regions) | Engine, every run; left as it is when the country is unknown |
| Territory Status | Picklist (In territory, Outside territory, Not checked) | Engine, every run |
| Territory Note | Text, 255 characters | Engine, every run; sent empty when the account is in territory, so an old note is cleared |
| BDR | Text | First load only |
| Account Owner (standard field) | User | First load only, and only when the BDR is a real user (see Account ownership) |

Account Name is also set on the first load only. The headquarters country is not written to Salesforce.

Because Segment and Region are engine fields, a change made to them by hand in Salesforce is overwritten on the next run. Exceptions belong to the "Overrides and history" item on the roadmap.

## Write-back rules

1. **Upsert by Sample Id.** Update the account if it exists, create it if not, so a rerun creates no duplicates.
2. **After the first load, write only engine fields:** Score, Tier, Top Reasons, Last Scored, Segment, Region, Territory Status and Territory Note. Name, BDR and Account Owner are never overwritten.
3. **Never delete or blank an account** because it was missing from a run.
4. **All or nothing, one request at a time.** Check every record before writing. If the engine's own check finds a bad record, write nothing at all. Salesforce takes at most 200 records in a batch and, by its REST guide, at most five such batches in one request, so one all-or-nothing request holds at most 1,000 records. Tested in the Developer org on 2026-11-14: one bad record in either of two batches made Salesforce undo both. A run of more than 1,000 records is sent as several requests of up to 1,000, each all or nothing. If Salesforce refuses a later request, the earlier ones stay saved, so Salesforce can hold a mix of this run's scores and the last run's until the run is repeated. The engine says how many accounts were written and how many were not, the run fails, and a rerun is safe. The requests go in order, and the engine stops at the first one that fails: if the first is refused, nothing is changed; if the second is refused, the first 1,000 are saved and the other 800 are not.
5. **Read back.** After writing, count the accounts that have this run's Last Scored time. Any number other than the number scored means the run failed. So that the count can only be this run's work, the engine also counts before writing: if any account already carries this run's time (an earlier run in the same second), it sends nothing and stops. A run succeeds only when Salesforce answers the write with a clean success and the count matches. When a run is sent as two requests, the engine counts after each one: 1,000 after the first, 1,800 after the second. If the second is refused, the count is what it reports as written. Any other answer fails the run whatever the count says. A write request that gets no answer also fails the run, and the engine says to run it again. What it reports as written depends on the count. If the count has not moved since the last request that was confirmed (0 for the first request, 1,000 for the second), nothing from the unanswered request was saved, and the engine reports that number as written. If the count has moved, the request may have been saved, but a count cannot prove the accounts came from this run, so the engine reports the written count as unknown. A rerun is safe.
6. **Fail loudly.** Print scored, written and failed counts. Any failure exits with an error, so GitHub Actions shows a red run.

## Account ownership

When the engine first creates an account, it looks for an active Salesforce user whose name is the same as the account's BDR. If there is one, that user becomes the Account Owner. If there is none, the account is owned by the engine's integration user, as today. The engine never changes the owner of an account that already exists, the same way it never overwrites Name or BDR. No Salesforce username is kept in the repository; the match is on the person's name. Reason: the BDR dashboard shows each viewer the accounts they own, so the two real BDR users have to own theirs, and ownership in a real org is managed in Salesforce, not by a scoring job.

Agreed: two BDRs are real users in the demo org. They come from different territories so their dashboards look different, and they carry generated sample names like the rest of the data. Both are on the cheaper Salesforce Platform licence, which can open the BDR dashboard and the Account Scoring app (tested on 2026-11-15).

## Dashboards

A Salesforce dashboard shows numbers as of the last time someone pressed Refresh. Both dashboards count every account that has been scored, meaning every account that has a tier, whoever created it. In v0.1 those are the 1,800 sample accounts; when the engine later scores accounts that a BDR or RevOps added in Salesforce, they appear with no change to the dashboards.

**The BDR dashboard ("my accounts").** One dashboard shared by every BDR. It loads its numbers as whoever is looking at it, and its reports are limited to the accounts the viewer owns, which is what makes each BDR see only their own. It has three tiles: my accounts by tier, as a chart with the total; my Tier A and B accounts, as a table sorted by score with name, tier, score and the date and time each was last scored; and my accounts outside my territory, as a table with the engine's note. Tier B is included because only about 4 in 100 accounts are Tier A, which would leave a typical BDR with one or none. Adding a BDR means adding a user; the dashboard does not change.

**The manager dashboard ("Team Account Scoring").** The whole team, in seven tiles: accounts scored; accounts outside their BDR's territory; accounts by tier; by segment and by region, each split by tier; Tier A and B accounts for each BDR, most first; and every Tier A account with its BDR, segment, region, score and the date and time it was last scored, which is where the dashboard shows the last scored time. "By BDR" counts Tier A and B accounts because every BDR holds the same number of accounts, so a plain count would say nothing.

**Who can see them.** A viewer needs the `Scoring_Viewer` permission set. It lets them read the ten scoring fields and open the Account Scoring app, and nothing else. Both dashboards are open to everyone inside the org.

**Where a BDR finds it.** The Account Scoring app opens on the BDR dashboard as its Home page. Salesforce returns a user to the app they used last, so a BDR picks the app once. No profile and no standard Salesforce app is changed.

**What "only their accounts" means.** It controls what the BDR dashboard shows. It does not stop a BDR from opening another BDR's account elsewhere in Salesforce: account sharing in the org stays as it is.

## Schedule

Weekdays at 7:00 AM Eastern, plus a manual Run button (GitHub Actions `workflow_dispatch`). Both run the same script. The start can be a few minutes late, because GitHub queues scheduled runs. The time zone is set in the workflow, so daylight saving needs no change.

## Secrets

Salesforce credentials live in GitHub Actions secrets and a local `.env` file that git ignores. They never go in code, commits or logs. On GitHub they are handed only to the steps that talk to Salesforce. Messages built from Salesforce replies show only Salesforce's error code, the account's Sample Id and the field name. Salesforce's own wording is never printed, because it is free text that can hold a person's name and no pattern can tell a name from other words. Whole replies are not printed. What is shown is still cleaned of addresses, ids and login names.

## Done when (v0.1 acceptance)

- [ ] `python run_local.py` prints tier counts for each segment that add up to 1,800, and the top 3 accounts in each segment with BDR, region and up to three reasons each. It also prints the territory check, with each flagged account and its note: 1,692 in territory, 90 outside and 18 not checked. Those three numbers follow from the agreed pattern and are confirmed when the data is generated.
- [ ] `pytest` passes, including the segment band edges (199, 200, 699, 700, 999, 1,000, 5,000, 5,001).
- [ ] Two runs in a row leave exactly 1,800 scored accounts in Salesforce, with no duplicates.
- [ ] One bad record among the 1,800 that the engine's own check catches leaves nothing written.
- [ ] When Salesforce refuses the second of the two requests, proven in the Developer org: the run fails, it reports the first 1,000 accounts as written and the other 800 as not written, the first 1,000 stay saved, and running again leaves all 1,800 carrying one Last Scored time.
- [ ] A forced failure (the Run button's "Force a failure" box, which feeds the engine a file with a duplicate id) turns the GitHub run red and leaves the existing scores unchanged.
- [x] The manager dashboard shows accounts by segment, BDR, region and tier, with the last scored time.
- [x] The BDR dashboard, opened as each of the two real BDR users, shows tier counts that add up to that user's 30 accounts, lists no account of another BDR, and differs between the two users. This check depends on open items 5 and 6.
- [ ] Demo video recorded and linked in the README and the changelog. It says why the sample is 1,800 accounts, what the demo environment limits (storage and logins), and how the design scales beyond it.

## Decisions and sources

The rows below were decided by the owner in chat on 2026-11-15. Everything else added to this spec that day is the writer's and is marked "Proposed, not agreed" or listed under Open items.

| Decision | Source |
|---|---|
| 60 BDRs with 30 accounts each, 1,800 accounts | chat, 2026-11-15: "60 bdrs x 30 accounts" |
| The demo says why the sample is small, how the design scales, and the limits the demo environment imposes | chat, 2026-11-15 |
| A BDR's dashboard shows only that BDR's accounts | chat, 2026-11-15 |
| One viewer dashboard shared by all BDRs | chat, 2026-11-15 |
| "Dashboard only": account sharing in the org is not made private | chat, 2026-11-15: "dashboard only" |
| Two real BDR logins, from different territories, with generated sample names; Platform licences tried first, the two full licences as the fallback | chat, 2026-11-15: "2 logins", in answer to that recommendation |
| Both dashboards in v0.1. The BDR dashboard shows my accounts by tier, my Tier A accounts with when they were last scored, and my accounts flagged as outside my territory. The manager dashboard shows the whole team. | chat, 2026-11-15: "both", in answer to that description |
| The README roadmap gains unassigned accounts (UAR): scored and ranked, and assigned to a BDR automatically when they reach Tier A or B | chat, 2026-11-15; the owner confirmed UAR means unassigned accounts |
| 1,800 accounts are written as two requests of up to 1,000, each all or nothing; a mix is possible if the second fails, the engine reports what was written, and a rerun fixes it | chat, 2026-11-15: "go with the first option" |
| Bulk API 2.0 goes on the roadmap, not into v0.1 | chat, 2026-11-15: "bulk api on the roadmap" |
| The engine sets the Account Owner once, when it first creates an account whose BDR has the same name as a Salesforce user, and never changes an owner afterwards | chat, 2026-11-15: agreed as item 1 |
| The BDR dashboard's reports are limited to accounts the viewer owns | chat, 2026-11-15: agreed as item 2 |
| The dashboards count every account that has been scored, whoever created it | chat, 2026-11-15: the owner's correction to item 3 |
| The two write requests go in order and the engine stops at the first that fails | chat, 2026-11-15: agreed as item 4 |
| A write request with no answer reports the written count as unknown only when the count has moved; when it has not moved, the engine reports the number already confirmed (0 or 1,000) | chat, during the scale-up build: "yes to all three", accepting the behaviour as built in slice su-2 |
| Error messages show Salesforce's error code, never its wording | chat, during the scale-up build: "yes, approve codes-only", after an independent check showed a person's name could reach a public log |
| Sample data pattern at 1,800: every 20th account outside its territory, every 100th from number 50 with an unknown country | chat, 2026-11-15: "yes to all four" |
| The 300 accounts in the org are replaced by deleting them through the admin login just before the first load, with the owner's yes at that moment | chat, 2026-11-15: "yes to all four" |
| The BDR dashboard has three tiles, and its account list covers Tier A and B, not Tier A alone | chat, 2026-11-15: "yes to all four"; this changes the earlier "my Tier A accounts" |
| The engine stays at five batches in one request and counts after each request | chat, 2026-11-15: "yes to all four" |
| Scoring accounts read from Salesforce goes on the roadmap in this order: BDR-owned accounts first, then unassigned (UAR) and all other accounts, then automatic assignment at Tier A or B, tightened to Tier A only if too many qualify | chat, 2026-11-15: "roadmap for the sequence" |
| "By BDR" on the manager dashboard shows the Tier A and B accounts of each BDR, most first | chat, 2026-11-15: "go", approving the dashboards plan with its recommendation |
| The BDR dashboard is the Home page of a new Account Scoring app; no profile and no standard app is changed | chat, 2026-11-15: "go with A" |
| A viewer needs the read-only `Scoring_Viewer` permission set, and the admin user holds it too | chat, 2026-11-15: "go", approving the plan; and "yes, add Scoring_Viewer to my admin user" |

## Open items

Each is open until the owner decides. None is a rule yet.

1. Settled on 2026-11-15: every 20th account outside its territory (90) and every 100th from number 50 with an unknown country (18). See In scope.
2. Settled on 2026-11-15: see write-back rule 4 and the Decisions table. One request holding nine batches is not allowed by Salesforce's REST guide.
3. Settled on 2026-11-15: the ownership rule under "Account ownership" is agreed. See the Decisions table.
4. **Replacing the 300 accounts now in the org.** The approach is settled: just before the first load of 1,800, a snapshot of the 300 is saved and they are deleted through the admin login (the engine's own user cannot delete). The deletion itself still needs the owner's yes at that moment.
5. **What the engine's integration user needs.** Read from the org on 2026-11-15: it can see the Account Owner field, the field is settable on create, and it can read user names. No change to the `Scoring_Engine` permission set is expected. Proven only by the first real write; if Salesforce refuses, the owner decides.
6. Settled on 2026-11-15: a Salesforce Platform licence can open the BDR dashboard, tested with both real BDR users, who each saw their own 30 accounts. It can also open the Account Scoring app, seen as one of the two.
7. Settled on 2026-11-15: "by BDR" shows the Tier A and B accounts of each of the 60 BDRs, most first. See Dashboards and the Decisions table.
8. Settled on 2026-11-15: the note is true. A saved test from 2026-11-14 shows this org accepted six batches in one request, while Salesforce's REST guide says five is the most. The engine stays at the documented five, and the code comment is reworded to say both.
9. Settled on 2026-11-15: the engine counts after each request. See write-back rule 5.
