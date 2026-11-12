# Spec: Account Scoring Engine v0.1

## Purpose

Rank a BDR team's accounts on transparent rules and put the result where reps already work: Salesforce. Every score has to explain itself.

## In scope for v0.1

- 300 generated sample accounts and 60 BDRs. Each BDR works one segment in one region: 5 segments x 4 regions (North America, EMEA, APAC, LATAM) x 3 BDRs, with 5 accounts each.
- Segment assignment by employee count, five scoring rules, tiers and top three reasons.
- Write-back of results to Salesforce Account in a free Developer org.
- A scheduled run plus a manual Run button through GitHub Actions.
- One dashboard by segment, BDR, region and tier.

## Not in v0.1 (see the README roadmap)

Email, Google Sheets, Slack, a web app, a stored run log, eligibility checks, AI-written summaries, real company data and large-scale tests.

## Input: one account

| Field | Type | Notes |
|---|---|---|
| id | text | Fixed ID; used to match records in Salesforce |
| name | text | Generated company name |
| region | text | North America, EMEA, APAC or LATAM |
| bdr | text | Generated BDR name; the BDR works this account's segment and region |
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

## Rules (in `weights.yaml`)

| Rule | Points | Group |
|---|---|---|
| Operates in 3 or more countries | 25 | Fit |
| Hourly-workforce industry (retail, hospitality, healthcare, manufacturing, logistics) | 25 | Fit |
| Hiring surge | 20 | Timing |
| New HR leader | 15 | Timing |
| Engaged in the last 14 days | 15 | Timing |

Tiers: **A** 75 and up, **B** 55 to 74, **C** 35 to 54, **D** under 35. Fit totals 50 at most, so Tier A always needs a timing signal.

No rule logic lives outside `weights.yaml`.

## Output: one scored account (in `output/scores.json`)

`id`, `segment`, `score`, `tier`, `top_reasons` (the three highest-point rules that matched, highest first), `rules_version`, `scored_at` (UTC time).

Ranking order: score from highest to lowest, then account `id`, so the order is the same on every run.

## Salesforce fields on Account

| Field | Type | Written by |
|---|---|---|
| Sample Id | Text, External ID, unique | First load only |
| Score | Number | Engine, every run |
| Tier | Picklist (A, B, C, D) | Engine, every run |
| Top Reasons | Long text | Engine, every run |
| Last Scored | Date/Time | Engine, every run |
| Segment | Picklist (the five segments) | First load only |
| BDR | Text | First load only |
| Region | Picklist | First load only |

## Write-back rules

1. **Upsert by Sample Id.** Update the account if it exists, create it if not, so a rerun creates no duplicates.
2. **After the first load, write only engine fields:** Score, Tier, Top Reasons and Last Scored.
3. **Never delete or blank an account** because it was missing from a run.
4. **All or nothing.** Check every record before writing. If any record is bad, write nothing.
5. **Read back.** After writing, count the accounts that have this run's Last Scored time. Fewer than expected means the run failed.
6. **Fail loudly.** Print scored, written and failed counts. Any failure exits with an error, so GitHub Actions shows a red run.

## Schedule

Weekdays at 7:00 AM Eastern, plus a manual Run button (GitHub Actions `workflow_dispatch`). Both run the same script.

## Secrets

Salesforce credentials live in GitHub Actions secrets and a local `.env` file that git ignores. They never go in code, commits or logs.

## Done when (v0.1 acceptance)

- [ ] `python run_local.py` prints tier counts for each segment that add up to 300, and the top 3 accounts in each segment with BDR, region and three reasons each.
- [ ] `pytest` passes, including the segment band edges (199, 200, 699, 700, 999, 1,000, 5,000, 5,001).
- [ ] Two runs in a row leave exactly 300 scored accounts in Salesforce, with no duplicates.
- [ ] A forced failure turns the GitHub run red and leaves the existing scores unchanged.
- [ ] The dashboard shows accounts by segment, BDR, region and tier, with the last scored time.
- [ ] Demo video recorded and linked in the README and the changelog.
