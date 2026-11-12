# Changelog

Every release is listed here, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow [Semantic Versioning](https://semver.org/).

Two version numbers exist, and they are separate:
- **Release version** (v0.1.0, v0.2.0…): the version of the whole project, tagged on GitHub.
- **Rules version** (`version` in `weights.yaml`): changes only when the scoring rules change. Every score records the rules version that made it.

Only list an item here once it's built and checked.

## [Unreleased]

## [0.1.0] - not yet released

Demo video: _link added at release_

### Added
- Sample data generator: 300 fictional accounts and 60 BDRs, each working one of 5 segments in one of 4 regions, fixed seed so every run makes the same data.
- Segments by employee count: SMB, Mid-Market, Sub-Enterprise, Enterprise and Large Enterprise.
- Scoring engine: five rules worth 100 points in `weights.yaml`, tiers A to D, top three reasons per account in plain English.
- Local run: tier counts and top 3 accounts per segment and `output/scores.json`.
- Tests for a full-match account, a no-match account, reason order and segment band edges.
- Salesforce write-back: Segment, BDR and Region on first load; Score, Tier, Top Reasons and Last Scored on Account, matched on a fixed ID so reruns create no duplicates.
- Scheduled GitHub Action with a manual Run button.
- Salesforce dashboard by segment, BDR, region and tier.
