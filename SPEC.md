# Spec: Account Scoring Engine v0.1

## Purpose

Rank a BDR team's accounts on transparent rules and put the result where reps already work: Salesforce. Every score has to explain itself.

## In scope for v0.1

- 300 generated sample accounts and 60 BDRs. Each BDR works one segment in one region: 5 segments x 4 regions (North America, EMEA, APAC, LATAM) x 3 BDRs, with 5 accounts each. Every 20th account (15 in all) is deliberately placed outside its BDR's territory: 5 by size, 5 by region and 5 by both. Three accounts have a headquarters country that is not in the country table.
- Segment assignment by employee count, five scoring rules, tiers and top three reasons.
- Write-back of results to Salesforce Account in a free Developer org.
- A scheduled run plus a manual Run button through GitHub Actions.
- Runs on Python 3.13, named in `.python-version`.
- One dashboard by segment, BDR, region and tier.

## Not in v0.1 (see the README roadmap)

Email, Google Sheets, Slack, a web app, a stored run log, eligibility checks, AI-written summaries, real company data and large-scale tests.

## Input: one account

| Field | Type | Notes |
|---|---|---|
| id | text | Fixed ID; used to match records in Salesforce |
| name | text | Generated company name |
| hq_country | text or empty | Country of the company's headquarters; decides the region |
| bdr | text | Generated BDR name; the BDR who holds the account in the sample data |
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

The engine only flags. It never changes the BDR or the Account Owner, and the score and tier do not depend on territory. An account outside its BDR's territory is ranked in the segment it truly belongs to.

Limits:

- The check compares against the BDR in the sample file. A BDR changed by hand in Salesforce is not seen. Reading the live owner back belongs to the reassignment item on the roadmap.
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

Account Name is also set on the first load only. The headquarters country is not written to Salesforce.

Because Segment and Region are engine fields, a change made to them by hand in Salesforce is overwritten on the next run. Exceptions belong to the "Overrides and history" item on the roadmap.

## Write-back rules

1. **Upsert by Sample Id.** Update the account if it exists, create it if not, so a rerun creates no duplicates.
2. **After the first load, write only engine fields:** Score, Tier, Top Reasons, Last Scored, Segment, Region, Territory Status and Territory Note. Name and BDR are never overwritten.
3. **Never delete or blank an account** because it was missing from a run.
4. **All or nothing.** Check every record before writing. If any record is bad, write nothing. Salesforce takes at most 200 records in a batch, so the batches travel in one request with "all or none" switched on. Tested in the Developer org on 2026-11-14: one bad record in either batch made Salesforce undo both. A run of more than 1,000 records stops instead of splitting across requests.
5. **Read back.** After writing, count the accounts that have this run's Last Scored time. Any number other than the number scored means the run failed. So that the count can only be this run's work, the engine also counts before writing: if any account already carries this run's time (an earlier run in the same second), it sends nothing and stops. A run succeeds only when Salesforce answers the write with a clean success and the count matches. Any other answer fails the run whatever the count says. A write request that gets no answer also fails the run: it may have been saved, but a count cannot prove the accounts came from this run, so the engine reports the written count as unknown and says to run it again. A rerun is safe.
6. **Fail loudly.** Print scored, written and failed counts. Any failure exits with an error, so GitHub Actions shows a red run.

## Schedule

Weekdays at 7:00 AM Eastern, plus a manual Run button (GitHub Actions `workflow_dispatch`). Both run the same script. The start can be a few minutes late, because GitHub queues scheduled runs. The time zone is set in the workflow, so daylight saving needs no change.

## Secrets

Salesforce credentials live in GitHub Actions secrets and a local `.env` file that git ignores. They never go in code, commits or logs. On GitHub they are handed only to the steps that talk to Salesforce. Messages built from Salesforce replies are cleaned of addresses, ids and user names before they are printed, and whole replies are not printed.

## Done when (v0.1 acceptance)

- [ ] `python run_local.py` prints tier counts for each segment that add up to 300, and the top 3 accounts in each segment with BDR, region and up to three reasons each. It also prints the territory check: 282 in territory, 15 outside and 3 not checked, with each flagged account and its note.
- [ ] `pytest` passes, including the segment band edges (199, 200, 699, 700, 999, 1,000, 5,000, 5,001).
- [ ] Two runs in a row leave exactly 300 scored accounts in Salesforce, with no duplicates.
- [ ] A forced failure (the Run button's "Force a failure" box, which feeds the engine a file with a duplicate id) turns the GitHub run red and leaves the existing scores unchanged.
- [ ] The dashboard shows accounts by segment, BDR, region and tier, with the last scored time.
- [ ] Demo video recorded and linked in the README and the changelog.
