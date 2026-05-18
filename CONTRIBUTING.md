# Contributing

Thanks for considering a contribution.

## Development

Set up the local environment:

```bash
./scripts/setup.sh
```

Run the checks before opening a pull request:

```bash
PYTHONPATH=src python3 -m unittest tests/test_core.py
python3 -m compileall -q src tests
npm --prefix frontend run build
bash -n scripts/setup.sh scripts/run.sh
git diff --check
```

## Pull Requests

- Keep changes focused and explain the user-facing behavior.
- Include tests for behavior changes.
- Do not commit local `.env`, `.logs`, `.locks`, build output, or machine-specific files.
- Update `README.md` when configuration or workflows change.

## Security and Secrets

Never include real API keys, Linear issue data, GitHub tokens, logs containing private code, or customer data in issues, pull requests, fixtures, or screenshots.
