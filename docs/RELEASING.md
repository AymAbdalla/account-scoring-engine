# How to cut a release

Do this every time a version ships.

## Before

- [ ] Everything for this version is merged into `main`, and `pytest` passes.
- [ ] The scheduled run is green, and the Run button works.
- [ ] `SPEC.md` matches what was actually built.
- [ ] If the scoring rules changed, bump `version` in `weights.yaml`.
- [ ] Decide whether the release needs a new demo video. New behaviour someone can see means yes; fixes and refactors mean no.

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
