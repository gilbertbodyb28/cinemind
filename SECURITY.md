# Security policy

## Scope

CineMind can handle authentication sessions, OAuth tokens, watch history, ratings and MediaManager credentials. Treat every deployment as handling personal data.

This is a pre-release project. It has not had an independent security audit and must not be presented as security-certified.

## Supported versions

Only the latest development commit and latest tagged release receive best-effort security fixes while the project is pre-1.0. Older commits may contain known issues.

## Reporting a vulnerability

Do not open a public issue for a vulnerability. Use GitHub private vulnerability reporting when enabled. Otherwise contact the repository owner through a private GitHub channel with the affected commit, reproduction, impact and mitigation. Never include real tokens, passwords, watch history or personal data.

## Deployment precautions

- Keep .env files, MongoDB data and provider tokens outside version control.
- Use HTTPS and appropriate secure cookie settings outside local development.
- Restrict CORS to the actual frontend origin.
- Use separate OAuth applications for development and production.
- Back up MongoDB securely and test restoration.
- Do not expose the backend or MongoDB publicly without an access-control plan.
