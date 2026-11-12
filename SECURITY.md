# Security policy

## Reporting

Please do not open a public issue for a suspected vulnerability. Email the
maintainers at `security@tweaker.dev` with:

- a description of the issue,
- the affected version or commit,
- reproduction steps or a proof of concept,
- the impact you believe it has.

We will acknowledge receipt within a few business days and coordinate a fix and
disclosure.

## Threat model

Tweaker sits between self-hosted Sentry and an LLM provider. It handles:

- Sentry event payloads, which can contain customer data and secrets,
- source code from connected repositories,
- Sentry and source-control credentials,
- model prompts and responses.

### Trust boundaries

- **Sentry → Tweaker** is authenticated by an HMAC-SHA256 signature over the raw
  body (`SEER_API_SHARED_SECRET`) and a signed `X-Viewer-Context` JWT.
- **Tweaker → Sentry** uses the Seer RPC endpoint with
  `SEER_RPC_SHARED_SECRET`.
- **Tweaker → model provider** is outbound HTTPS (or a configured local
  endpoint). Telemetry is redacted before it leaves the process.
- **Tweaker → source control** uses tokens from the environment. Tokens are
  passed to git through a short-lived `GIT_ASKPASS` script, not argv.

### Guardrails

- Patches are rejected if they contain absolute paths, `..`, `.git`, binary
  changes, file deletions, or symlinks.
- Patches are checked with `git apply --check` before publication.
- Repository reads are constrained to the checkout root.
- Generated code is never merged automatically; publishing creates a branch and
  a PR/MR for human review.
- The operator console is optional and can be protected with
  `TWEAKER_AUTH_TOKEN`.
- Seer API requests are rate-limited per IP and request bodies are size-limited.
- Model usage is recorded per organization and can be capped with monthly
  token, cost, and run quotas.

### Known limitations

- Prompt injection is mitigated with delimited untrusted context and
  instructions, but model output is still untrusted. Always review patches.
- The Seer contract is internal; a malicious or compromised Sentry deployment
  that holds the shared secrets can invoke Seer endpoints. Rotate secrets if a
  deployment is compromised.
- Tweaker does not execute repository test commands in this release. Validation
  is limited to diff parsing and `git apply --check`.

## Deployment checklist

- Set strong, distinct `SEER_API_SHARED_SECRET` and `SEER_RPC_SHARED_SECRET`
  values (32+ random bytes).
- Set `TWEAKER_SECRET_KEY` explicitly and back it up.
- Put the operator console behind your SSO/reverse proxy and set
  `TWEAKER_AUTH_TOKEN`.
- Use TLS between Sentry, Tweaker, and any remote model endpoint.
- Run the container as a non-root user (the provided image does).
- Restrict `TWEAKER_GITHUB_TOKEN` / `TWEAKER_GITLAB_TOKEN` to the repositories
  Tweaker needs.
- Review the audit log and run history for unexpected activity.
