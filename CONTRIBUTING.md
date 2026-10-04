# Contributing to CineMind

Thank you for helping make CineMind more useful and more trustworthy. Documentation, tests, provider fixes, accessibility improvements and bug fixes are especially valuable.

## Before opening an issue or pull request

1. Search existing issues and pull requests.
2. Reproduce the problem with demo data where possible.
3. Remove credentials, OAuth tokens, private URLs and personal watch history.
4. Read AGENTS.md, including the permanent Vision UI lock.

## Development setup

Follow README.md. The public path uses Python 3.11+, Node.js 22+, MongoDB and deploy/nas/requirements.txt plus backend/requirements-dev.txt.

Before submitting a change, run:

    cd frontend
    yarn test --watchAll=false --runInBand
    yarn lint
    yarn build
    cd ..
    python -m pytest backend/recommendation_tests backend/evaluation_tests -q
    python3 scripts/check_vision_ui_lock.py
    git diff --check

Provider integrations should use fixtures or mocks. CI and pull requests must not depend on personal API keys.

## Pull requests

- Keep one logical change per pull request.
- Explain user-visible behavior and verification performed.
- Add or update tests for behavior changes.
- Update documentation and changelog when the public workflow changes.
- Do not modify frozen Vision UI files unless Gilbert explicitly unlocks a named visual change.
- Do not add fake stars, fake contributors, fake testimonials, generated engagement or unverifiable adoption claims.
- Do not claim maintainer, core-team or official integration status without explicit authorization.

## Attribution

Git history and merged pull requests are the source of truth for contribution credit. Ask contributors before changing their public attribution.
