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
- Sample data generator: 300 fictional accounts, 60 BDRs across 4 regions, fixed seed so every run makes the same data.
- Scoring engine: six rules worth 100 points in `weights.yaml`, tiers A to D, top three reasons per account in plain English.
- Local run: tier counts, top 10 accounts and `output/scores.json`.
- Tests for a full-match account, a no-match account and reason order.
- Salesforce write-back: Score, Tier, Top Reasons and Last Scored on Account, matched on a fixed ID so reruns create no duplicates.
- Scheduled GitHub Action with a manual Run button.
- Salesforce dashboard by BDR, region and tier.
