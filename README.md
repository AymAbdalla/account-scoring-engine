# Account Scoring Engine

**Scores every account on rules you can read, then writes the score, tier and top three reasons into Salesforce on a schedule, so each BDR knows where to look first.**

▶ Demo video: _link added at release_ · Latest release: _v0.1.0 (in progress)_ · [Changelog](CHANGELOG.md) · [Spec](SPEC.md)

_Dashboard screenshot goes here at release._

## Architecture at a glance

![Architecture: GitHub Actions starts the Python scoring engine on a schedule or on demand. It reads sample accounts and signals plus weights.yaml, sorts each account into a segment, scores it, picks the top 3 reasons and upserts the results to Salesforce, where 60 BDRs see ranked lists.](docs/architecture.svg)

GitHub starts the engine on a schedule or on demand. Nothing in a run needs a person, a laptop or a chat window.

## How it works

- **Each segment has its own reps.** Accounts are sorted into five segments by employee count (SMB to Large Enterprise), and each BDR works one segment in one region. A score ranks an account against the rest of its rep's book, so a small company isn't marked down for being small.
- **Rules live in one file.** `weights.yaml` holds the segment bands, five rules worth 100 points and the tier cutoffs. Changing a weight changes no code, and every score records which rules version made it.
- **Every score explains itself.** Each account gets a score, a tier (A to D) and the three rules that earned it the most points, in plain English.
- **Runs on its own.** A scheduled GitHub Action scores all accounts and writes the results into Salesforce. A Run button triggers it by hand. It only updates the fields it owns, it's safe to run twice, and it fails loudly if anything goes wrong.

## Quickstart

1. Set up Python: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. Make the sample data: `python data/generate_sample.py`
3. Score it: `python run_local.py` (prints tier counts and the top 3 accounts for each segment, and saves `output/scores.json`)

All data in this repo is generated sample data. No real company or person is in it.

## What the score means

The score tells a BDR **where to look first**. It is not a win probability, and it doesn't clear an account for outreach. Ownership, customer status and opt-outs belong to a separate eligibility check (see the roadmap).

Company size decides **who works the account**, not how good it is. The engine puts each account in a segment, and the score ranks it inside that segment:

| Segment | Employees |
|---|---|
| SMB | 50 to 199 |
| Mid-Market | 200 to 699 |
| Sub-Enterprise | 700 to 999 |
| Enterprise | 1,000 to 5,000 |
| Large Enterprise | 5,001 and up |

Fit rules (countries and industry) add up to 50 points at most, so **an account can't reach Tier A on fit alone**. It needs a timing signal: a hiring surge, a new HR leader or recent engagement.

Missing data scores zero but is reported as unknown, never as a negative signal.

## Who owns which field

| Field | Owner | The engine… |
|---|---|---|
| Score, Tier, Top Reasons, Last Scored | Engine | writes on every run |
| Segment, BDR, Region | Salesforce (in this demo, loaded once from sample data) | never overwrites |
| Account Owner | Salesforce | never touches |

## Roadmap

Each step ships as its own release with a changelog entry, and gets a new demo video when there's something worth showing.

1. **Eligibility and work queues:** customers, opt-outs and disqualified accounts leave the queue with a recorded reason. Accounts sort into Act now, Develop (good fit, no timing yet) and Monitor.
2. **Account research and plan drafts:** a scheduled job researches top-tier accounts and drafts a short plan for each. Nothing is saved until a person approves it.
3. **Real signals:** a signal listener watches public sources for hiring and leadership changes and feeds the engine, replacing the sample flags.
4. **Call prep cards:** a daily brief for each contact on today's call list, with the verified signal, an opening question and gaps marked as gaps.
5. **Overrides and history:** a person can change a tier with a reason and an end date, with the original score kept, and tier changes are tracked over time.
6. **Measurement:** first a shadow run next to the current process, then meetings held and accepted pipeline per account worked, with coverage shown beside results.
7. **Rules per segment:** each segment gets its own weights in `weights.yaml`, for example more points for multi-country operations in Enterprise than in SMB.
8. **Scale:** rescore only the accounts that changed, plus a 50,000-account test.
