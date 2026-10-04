# Release checklist

This checklist is intentionally manual. It prevents an unpublished release candidate from being represented as a shipped release.

- [ ] Choose a clean release baseline; do not include unrelated working-tree changes.
- [ ] Confirm README, LICENSE, CONTRIBUTING.md and SECURITY.md are current.
- [ ] Run frontend test, lint and build.
- [ ] Run backend unit/evaluation tests in a clean Python 3.11 environment.
- [ ] Run the Vision UI lock check and `git diff --check`.
- [ ] Verify a fresh demo installation using only documented public dependencies.
- [ ] Review provider terms, artwork attribution and privacy implications.
- [ ] Update CHANGELOG.md and `docs/releases/v0.1.0.md` with only verified facts.
- [ ] Create tag `v0.1.0` on the clean release commit.
- [ ] Publish the GitHub Release only after the tag and CI are visible.
- [ ] Record the published URL and date; do not claim release before then.
- [ ] Announce only with the approved draft in `docs/community-post-draft.md`.
