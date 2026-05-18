# Security Policy

## Reporting a Vulnerability

Please do not open a public issue for sensitive security reports.

Email the maintainers with:

- A description of the issue.
- Steps to reproduce.
- Impact and affected versions, if known.
- Any relevant logs with secrets removed.

## Secret Handling

This project can use local credentials for Codex, Linear, and GitHub. Keep these values out of the repository:

- `LINEAR_API_KEY`
- GitHub tokens
- Codex credentials
- `.env`
- `.logs/`
- `.locks/`

If a secret is committed accidentally, revoke it immediately and rotate the affected credential.
