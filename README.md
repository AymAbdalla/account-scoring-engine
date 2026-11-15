# Account Scoring Engine

**Scores every account on rules you can read, then writes the score, tier and top three reasons into Salesforce on a schedule, so each BDR knows where to look first.**

▶ Demo video: _link added at release_ · Latest release: _v0.1.0 (in progress)_ · [Changelog](CHANGELOG.md) · [Spec](SPEC.md)

**Status:** the scoring engine, the territory check and the Salesforce write-back are built and tested. The scheduled run, the dashboard and the demo video are still to come before v0.1.0 is released.

_Dashboard screenshot goes here at release._

## Architecture at a glance

![Architecture: GitHub Actions starts the Python scoring engine on a schedule or on demand. It reads sample accounts and signals plus weights.yaml, sorts each account into a segment, scores it, picks the top 3 reasons and upserts the results to Salesforce, where 60 BDRs see ranked lists.](docs/architecture.svg)

The design is for GitHub to start the engine on a schedule or on demand, so that nothing in a run needs a person, a laptop or a chat window. Today one command starts it; the scheduled run is the next piece to build.

## How it works

- **Each territory has its own reps.** A territory is a company-size segment in a headquarters region, for example "Enterprise, EMEA". The engine works out both from the account: the segment from employee count and the region from headquarters country. A score ranks an account inside its own segment, so a small company isn't marked down for being small.
- **Accounts in the wrong book get flagged.** The engine compares each account's territory with the territory of the BDR who holds it. A mismatch is flagged with the evidence, such as "205 employees is Mid-Market; BDR covers SMB". It only flags. It never moves an account.
- **Rules live in one file.** `weights.yaml` holds the segment bands, the regions and their countries, five rules worth 100 points and the tier cutoffs. Changing a weight changes no code, and every score records which rules version made it.
- **Every score explains itself.** Each account gets a score, a tier (A to D) and up to three rules that earned it the most points, in plain English. An account that matched no rule says "No rules matched".
- **Built to run unattended.** One command scores all accounts and writes the results into Salesforce. It only updates the fields it owns, it's safe to run twice, and it fails loudly if anything goes wrong. A scheduled GitHub Action with a Run button comes next.

## Quickstart

1. Set up Python 3.13 (the version named in `.python-version`): `python3.13 -m venv .venv && source .venv/bin/activate && pip install --require-hashes -r requirements.lock`. The lock file lists every library with a checksum, and pip refuses any file that doesn't match.
2. Make the sample data: `python data/generate_sample.py`
3. Score it locally: `python run_local.py` (prints tier counts, the top 3 accounts for each segment and the accounts outside their BDR's territory, and saves `output/scores.json`)

To write the scores into a Salesforce Developer org as well:

4. Deploy the ten fields and the permission set: `sf project deploy start --source-dir force-app --target-org <your org>`
5. In Salesforce Setup, create a user for the engine on the Salesforce Integration licence, give it the Salesforce API Integration permission set licence, and assign it the `Scoring_Engine` permission set. The engine logs in as this user, which can read, create and edit accounts but not delete them.
6. In Setup, create an External Client App with the client credentials flow switched on, running as that user.
7. Copy `.env.example` to `.env` and fill in the org address and the app's key and secret. Git ignores `.env`.
8. Check the connection: `python check_salesforce.py` (reads only)
9. Write and read back: `python write_back.py` (creates the 300 accounts the first time, then updates the same 300 on every later run)

All data in this repo is generated sample data. Names come from a fake-data library, so any match with a real company or person is a coincidence.

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

Fit rules (countries and industry) add up to 50 points at most, so **an account can't reach Tier A on fit alone**. It needs at least two of the three timing signals: a hiring surge, a new HR leader or recent engagement. Full fit plus one timing signal tops out at 70, which is Tier B.

Missing data scores zero but is reported as unknown, never as a negative signal.

## What the territory check means

Every account gets one of three statuses:

| Status | Meaning |
|---|---|
| In territory | The account's segment and region match its BDR's territory |
| Outside territory | The size, the region or both don't match; the note says which and shows the number or country |
| Not checked | The engine couldn't tell, for example the headquarters country isn't in its country table. It says so and doesn't guess |

The check doesn't change the score. An account that has outgrown its BDR's size band keeps its score and is ranked in the segment it now belongs to.

Three limits worth knowing. The check compares against the BDR in the sample data, so it doesn't see a BDR changed by hand in Salesforce. An account one employee over a band edge is flagged the same as one far over, which is why the note shows the number. And real territories can also use named accounts and state or postcode carve-outs; this project uses size plus headquarters region to keep the rule readable.

## Who owns which field

| Field | Owner | The engine… |
|---|---|---|
| Score, Tier, Top Reasons, Last Scored | Engine | writes on every run |
| Segment, Region, Territory Status, Territory Note | Engine | works out and writes on every run, so a change made by hand is overwritten |
| Name, BDR | Salesforce (in this demo, loaded once from sample data) | never overwrites |
| Account Owner | Salesforce | never touches |

## Roadmap

Each step ships as its own release with a changelog entry, and gets a new demo video when there's something worth showing. All of it runs on sample data.

**v0.2: Signal buckets, closed-lost re-engage and work queues**

- **Buckets:** each account gets one dated signal type (M&A, new leadership hire, hiring surge, funding, expansion). Rules score timing from it.
- **Closed-lost re-engage:** a lost account keeps its loss reason and gets a re-engage date set by that reason. Budget waits for the next fiscal year, bad timing waits about 6 months, losing to a competitor waits until before their renewal, and no decision maker waits for a new-leader signal.
- **Back with history:** until that date the account stays out of the queue, then it comes back with its history attached.
- **Eligibility and work queues:** customers, opt-outs and disqualified accounts leave the queue with a recorded reason. Accounts sort into Act now, Develop (good fit, no timing yet) and Monitor.

**v0.3: Learn from wins and losses**

- **Closed-won lookalikes:** save what each won account looked like when it was evaluated (size, segment, bucket, signals), then score open accounts on how closely they match that snapshot today.
- **Closed-lost patterns:** count loss reasons by segment and bucket, and lower the weight of patterns that keep losing.
- **A person decides:** weight changes are proposed for a person to approve, saved with a new rules version, and never applied automatically.

**v0.4: AI research, outreach and call prep for Tier A only**

- **Tier A only, to control token spend:** a scheduled job drafts a research brief, a short account plan, a personalized email, a call script, and a call book entry with an outreach task.
- **Built on what the engine knows:** each draft uses the account's bucket, its closest won lookalike and any loss history.
- **Call prep cards:** a daily brief for each contact on today's call list, with the verified signal, an opening question and gaps marked as gaps.
- **A person approves every draft:** nothing is saved until then, every claim keeps its source and date, and thin research becomes a review card instead of a made-up script.

**Later, in no fixed order**

- **Territory reassignment:** when an account sits outside its rep's territory, propose a rep who covers it and let a person approve the move. The engine never changes an owner by itself.
- **Real signals:** a signal listener watches public sources for hiring and leadership changes and feeds the engine, replacing the sample flags.
- **Overrides and history:** a person can change a tier with a reason and an end date, with the original score kept, and tier changes are tracked over time.
- **Measurement:** first a shadow run next to the current process, then meetings held and accepted pipeline per account worked, with coverage shown beside results.
- **Rules per segment:** each segment gets its own weights in `weights.yaml`, for example more points for multi-country operations in Enterprise than in SMB.
- **Scale:** rescore only the accounts that changed, plus a 50,000-account test.
