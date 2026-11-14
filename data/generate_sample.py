"""Makes the sample data: 300 made-up accounts in data/accounts.json.

60 BDRs, each working one segment in one region (5 segments x 4 regions x 3 BDRs;
the segments and regions come from weights.yaml),
with 5 accounts each. Every name comes from Faker, a fake-data library, so any
match with a real company or person is a coincidence.

Also writes data/bdrs.json, the roster: which segment and region each BDR covers.

Each account gets a headquarters country from its BDR's region. Then, so the
territory check has something to find:
  - every 20th account (ACC-0020, ACC-0040, ... 15 in all) is moved outside its
    BDR's territory, taking turns between three cases: wrong size, wrong
    region, both;
  - ACC-0050, ACC-0150 and ACC-0250 get a country that is not in the country
    table in weights.yaml.

The seed below fixes the "random" choices, so this script writes exactly the
same files every time it runs.

How often each signal shows up in the sample (these are made-up rates, chosen
so that all four tiers appear):
  - hourly-workforce industry: 5 of the 10 industries, so about 50%
  - 3 or more countries:       50%
  - hiring surge:              30%
  - new HR leader:             20%
  - engagement unknown:        20%
  - engaged in last 14 days:   25%
  - engaged longer ago:        55%

Run from the project folder:  python data/generate_sample.py
"""
import json
import random
from pathlib import Path

import yaml
from faker import Faker

SEED = 42
PROJECT_FOLDER = Path(__file__).resolve().parent.parent
RULES_PATH = PROJECT_FOLDER / "weights.yaml"
OUTPUT_PATH = PROJECT_FOLDER / "data" / "accounts.json"
ROSTER_PATH = PROJECT_FOLDER / "data" / "bdrs.json"

BDRS_PER_SEGMENT_AND_REGION = 3
ACCOUNTS_PER_BDR = 5
LARGEST_COMPANY = 50000  # the top segment has no upper limit, so the sample needs one

INDUSTRIES = [
    "retail",
    "hospitality",
    "healthcare",
    "manufacturing",
    "logistics",
    "software",
    "financial services",
    "professional services",
    "media",
    "education",
]

MOVE_EVERY = 20  # every 20th account is moved outside its BDR's territory
MOVE_CASES = ["size", "region", "both"]  # the moved accounts take turns between these
UNKNOWN_COUNTRY_ACCOUNTS = [50, 150, 250]  # these get a country the table does not know
UNKNOWN_COUNTRY = "Iceland"

CHANCE_MANY_COUNTRIES = 0.50
CHANCE_HIRING_SURGE = 0.30
CHANCE_NEW_HR_LEADER = 0.20
CHANCE_ENGAGEMENT_UNKNOWN = 0.20
CHANCE_ENGAGED_RECENTLY = 0.25


def unique_name(make_name, used):
    """Ask Faker for names until it gives one we have not used yet."""
    name = make_name()
    while name in used:
        name = make_name()
    used.add(name)
    return name


def pick_days_since_engaged():
    """None means we don't know when the account last opened or clicked an email."""
    roll = random.random()
    if roll < CHANCE_ENGAGEMENT_UNKNOWN:
        return None
    if roll < CHANCE_ENGAGEMENT_UNKNOWN + CHANCE_ENGAGED_RECENTLY:
        return random.randint(0, 14)
    return random.randint(15, 180)


def make_accounts():
    # Seed both Faker and Python's random so every run makes the same choices.
    random.seed(SEED)
    Faker.seed(SEED)
    fake = Faker("en_US")

    # Read the size bands and the regions from weights.yaml so they are
    # written down in one place only.
    with open(RULES_PATH) as rules_file:
        rules = yaml.safe_load(rules_file)
    segments = rules["segments"]
    regions = rules["regions"]
    countries_by_region = rules["countries_by_region"]

    # A second, separate source of random choices for the headquarters country
    # and the moved accounts. Keeping it separate means every other value in
    # the file (names, signals, employee counts) stays exactly as it was.
    extra = random.Random(SEED)

    accounts = []
    roster = {}
    used_bdr_names = set()
    used_company_names = set()

    for segment in segments:
        smallest = segment["min"]
        largest = segment["max"] or LARGEST_COMPANY
        for region in regions:
            for _ in range(BDRS_PER_SEGMENT_AND_REGION):
                bdr = unique_name(
                    lambda: "{} {}".format(fake.first_name(), fake.last_name()),
                    used_bdr_names,
                )
                roster[bdr] = {"segment": segment["name"], "region": region}
                for _ in range(ACCOUNTS_PER_BDR):
                    number = len(accounts) + 1  # 1 for ACC-0001, 2 for ACC-0002, ...
                    many_countries = random.random() < CHANCE_MANY_COUNTRIES
                    employees = random.randint(smallest, largest)
                    hq_country = extra.choice(countries_by_region[region])

                    if number % MOVE_EVERY == 0:
                        move = MOVE_CASES[(number // MOVE_EVERY - 1) % len(MOVE_CASES)]
                        if move in ("size", "both"):
                            # An employee count from the next size band up
                            # (or the one below, for the largest band).
                            position = segments.index(segment)
                            other = segments[position + 1] if position + 1 < len(segments) else segments[position - 1]
                            employees = extra.randint(other["min"], other["max"] or LARGEST_COMPANY)
                        if move in ("region", "both"):
                            # A country from one of the other regions.
                            other_region = extra.choice([name for name in regions if name != region])
                            hq_country = extra.choice(countries_by_region[other_region])
                    if number in UNKNOWN_COUNTRY_ACCOUNTS:
                        hq_country = UNKNOWN_COUNTRY

                    accounts.append(
                        {
                            "id": "ACC-{:04d}".format(number),
                            "name": unique_name(fake.company, used_company_names),
                            "hq_country": hq_country,
                            "bdr": bdr,
                            "employees": employees,
                            "countries": random.randint(3, 12) if many_countries else random.randint(1, 2),
                            "industry": random.choice(INDUSTRIES),
                            "hiring_surge": random.random() < CHANCE_HIRING_SURGE,
                            "new_hr_leader": random.random() < CHANCE_NEW_HR_LEADER,
                            "days_since_engaged": pick_days_since_engaged(),
                        }
                    )
    return accounts, roster


if __name__ == "__main__":
    accounts, roster = make_accounts()
    with open(OUTPUT_PATH, "w") as output_file:
        json.dump(accounts, output_file, indent=2)
        output_file.write("\n")
    with open(ROSTER_PATH, "w") as roster_file:
        json.dump(roster, roster_file, indent=2)
        roster_file.write("\n")
    print("Wrote {} accounts to {}".format(len(accounts), OUTPUT_PATH.relative_to(PROJECT_FOLDER)))
    print("Wrote {} BDRs to {}".format(len(roster), ROSTER_PATH.relative_to(PROJECT_FOLDER)))
