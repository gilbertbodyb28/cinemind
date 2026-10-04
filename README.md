+# CineMind

CineMind is a self-hosted media discovery and recommendation companion for people who maintain their own movie and TV libraries. It combines watch history, ratings, provider metadata, local AI and MediaManager actions into one workflow.

> Status: pre-release (0.1.0 preparation). CineMind is usable for local evaluation, but the public release gate is not complete until CI, clean-install verification and the first external feedback cycle are finished.

## What it does

- Imports taste signals from Plex, Trakt, Simkl and AniList when configured.
- Uses TMDb and provider metadata to enrich movies, shows, people, genres and premiere dates.
- Produces explainable recommendations with filters for media type, genre, release window and user intent.
- Supports natural-language and AI-assisted discovery through a local Ollama model.
- Keeps saved recommendations, requests, approval history, jobs and runtime notices in one UI.
- Can send approved movies and series to a compatible MediaManager instance.
- Includes clearly marked demo mode so the UI can be explored before a provider is connected.

The demo data is synthetic seed data. It is never evidence of real users, real usage or real community adoption.

## Demo

The shortest honest demo is:

1. Start MongoDB and the backend with DEMO_MODE=true.
2. Start the frontend.
3. Open http://localhost:3000.
4. Explore the seeded home shelf, recommendations, taste profile, jobs and request flow.

No public hosted demo is claimed yet. Any future screenshot or recording must identify whether it shows demo data or a real connected account.

## Architecture

React 19 + CRACO frontend -> HTTP/session cookie -> FastAPI backend -> MongoDB

The backend connects optionally to TMDb, Trakt, Simkl, AniList, Plex, Ollama and MediaManager. The frontend is in frontend/. Backend and recommendation code is in backend/. Deployment material is in deploy/ and operational scripts are in scripts/.

## Requirements

- macOS or Linux
- Python 3.11+
- Node.js 22+
- MongoDB 7+ (local or container)
- Git

Optional integrations require their own accounts or API credentials. Google OAuth is only needed when that authentication flow is enabled.

## Local setup

1. Clone and configure:

    git clone https://github.com/gilbertbodyb28/cinemind.git CineMind
    cd CineMind
    cp backend/.env.example backend/.env

Edit backend/.env. The demo path only needs MongoDB, database settings, local CORS and DEMO_MODE=true. Never commit .env, OAuth secrets, provider tokens or exported user data.

2. Install backend dependencies:

    python3.11 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -r deploy/nas/requirements.txt -r backend/requirements-dev.txt

3. Start MongoDB. The default is mongodb://localhost:27017 with database cinemind.

4. Start the backend:

    cd backend
    PYTHONPATH=. python -m uvicorn server:app --reload --host 127.0.0.1 --port 8001

5. In another terminal, start the frontend:

    cd frontend
    yarn install --frozen-lockfile
    yarn start

Open http://localhost:3000.

## Production build and NAS image

    cd frontend && yarn install --frozen-lockfile && yarn build
    cd ..
    docker build -f deploy/nas/Dockerfile -t cinemind:local .
    docker run --rm --env-file backend/.env -p 8001:8001 cinemind:local

The image expects MongoDB through MONGO_URL. docs/autostart.md and deploy/mediamanager/README.md are environment-specific operational notes, not a universal installation contract.

## Tests and checks

    cd frontend
    yarn test --watchAll=false --runInBand
    yarn lint
    yarn build
    cd ..
    python -m pytest backend/recommendation_tests backend/evaluation_tests -q
    python -m pytest backend/tests -q
    python3 scripts/check_vision_ui_lock.py
    git diff --check

Some backend tests are integration-oriented and may require MongoDB or mocked provider services. CI keeps live provider credentials out of pull requests.

## Privacy and security

CineMind can process watch history, ratings, OAuth tokens and MediaManager credentials. Read SECURITY.md before deploying it for real data. The current pre-release must not be described as independently security-audited.

## Contributing

Bug reports, documentation improvements, tests and provider fixes are welcome. Read CONTRIBUTING.md first. Contributors must not add fake usage, fake stars, fabricated testimonials or unverified maintainer/contributor claims.

## Project status and honesty policy

GitHub-generated stars, forks, contributors, issue history and release downloads are the source of truth for those metrics. CineMind does not buy, exchange or automate engagement. A person is credited as a contributor only through actual Git history or consented public attribution.

## License and third-party services

CineMind is licensed under the MIT License (LICENSE). CineMind is not affiliated with TMDb, Trakt, Simkl, Plex, AniList, Ollama or MediaManager. Their APIs, data, artwork and trademarks remain subject to their own terms.

See CHANGELOG.md for release history and docs/community-post-draft.md for an unpublished community announcement draft.
