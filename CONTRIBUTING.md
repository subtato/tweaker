# Contributing

Thanks for helping build Tweaker.

## Development setup

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
cd web && npm install && npm run typecheck
```

The Python package lives in `src/tweaker`; the optional console is in `web/`.

## Compatibility work

The most valuable contributions are Seer-contract fixes:

- Capture a request/response pair from the Sentry release you run.
- Add a focused test in `tests/test_seer_compat.py` with the exact wire shape.
- Keep the implementation tolerant: unknown fields are ignored, but required
  fields are validated.
- Update `docs/seer-compatibility.md`.

Do not paste production telemetry, customer code, tokens, or full Sentry event
payloads into issues or tests. Use the synthetic Acme Store fixture.

## Code style

- Python: Ruff, 100-column lines, type annotations on public functions.
- TypeScript: strict mode, no `any` unless the wire payload is genuinely opaque.
- Prefer small, composable services over framework magic.
- Never log credentials, raw Sentry tokens, or full model prompts containing
  customer data.

## Security

See [SECURITY.md](SECURITY.md). Report vulnerabilities privately.

## License

By contributing, you agree that your contributions are licensed under the
Apache-2.0 license.
