# How to cut a release

Do this every time a version ships.

## Before

- [ ] Everything for this version is merged into `main`, and `pytest` passes.
- [ ] The scheduled run is green, and the Run button works.
- [ ] `SPEC.md` matches what was actually built.
- [ ] If the scoring rules changed, bump `version` in `weights.yaml`.
- [ ] Decide whether the release needs a new demo video. New behaviour someone can see means yes; fixes and refactors mean no.

## While the schedule is on

- [ ] Look at the **Actions** page once a week. A red run needs reading; a missing run means GitHub dropped it, and the Run button catches up.
- [ ] Two scheduled runs red in a row: switch the schedule off with `gh workflow disable score-accounts.yml` until the cause is fixed.
- [ ] Once a month, check whether the pinned actions (`actions/checkout`, `actions/setup-python`) and the libraries in `requirements.lock` have newer releases or security fixes. Nothing proposes these updates automatically.

## Before making the repository public

Anyone with a GitHub account can read a public repository's code, history, pull requests and run logs.

- [ ] Delete every workflow run made while the repository was private (**Actions**, then each run's menu, **Delete workflow run**).
- [ ] Start one fresh run and read its log from top to bottom.
- [ ] Scan the whole history and the text of every pull request for local paths, user names, the org's address and ids, and credentials.
- [ ] In **Settings**, **Actions**, **General**: require approval for workflows from outside contributors, and keep the default workflow permissions on read-only.
- [ ] Check that the three secrets are still listed after the change (`gh secret list` shows names only).
- [ ] Know the 60-day rule: in a public repository GitHub switches scheduled workflows off after 60 days without activity. `gh workflow enable score-accounts.yml` switches it back on.

## Changelog

- [ ] Move the shipped items from `[Unreleased]` into a new version heading with today's date.
- [ ] List only things that are built and checked.
- [ ] Add the demo video link, or "No new demo" for this release.

## Demo video (when needed)

- [ ] Record 2 to 3 minutes: the problem, the run, the dashboard and what's next.
- [ ] Use sample data only.
- [ ] Upload it, then paste the link into the README, the changelog and the GitHub Release.

## Tag and release

Commit the changelog:

```
git commit -am "chore: release v0.1.0"
```

Tag the release:

```
git tag v0.1.0
```

Push the commit and the tag:

```
git push && git push --tags
```

Then, on GitHub, go to **Releases** and choose **Draft a new release**. Pick the tag, paste that version's changelog section, add the demo link and publish.

## After

- [ ] Update "Latest release" at the top of the README.
- [ ] Add one line on what shipped to the portfolio and resume notes.
