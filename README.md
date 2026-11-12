# Tweaker

**A drop-in, open-source Seer service for self-hosted Sentry.**

Sentry's Seer UI, Autofix flow, issue summaries, and Seer Agent all speak a private
HTTP contract to a backend service. On Sentry SaaS that service is closed source.
Tweaker implements that service contract so a self-hosted Sentry installation can
keep its existing Seer UI and workflows while pointing them at an open-source
backend you control.

> Tweaker is not affiliated with or endorsed by Sentry. "Sentry" and "Seer" are
> trademarks of Functional Software, Inc.

## What "drop-in" means

Sentry's open-source code already contains the Seer client, feature flags, issue
details UI, Autofix drawer, Seer Agent entry point, and the RPC endpoints Seer uses
to read Sentry data. Tweaker supplies the missing server:

```
Sentry UI ──► Sentry /api/0/organizations/.../autofix/ ──► SEER_AUTOFIX_URL
                                                              │
                                                              ▼
                                                        ┌──────────┐
                                                        │ Tweaker  │
                                                        │  /v1/... │
                                                        └────┬─────┘
                                                             │ signed RPC
                                                             ▼
                                                    Sentry /api/0/seer-rpc/
```

Point `SEER_AUTOFIX_URL` and `SEER_SUMMARIZATION_URL` at Tweaker, share the same
`SEER_API_SHARED_SECRET` and `SEER_RPC_SHARED_SECRET`, and use Sentry's own Seer
settings and issue page. Tweaker also includes an optional operator console for
connection health, recent runs, and local debugging; it is not required for the
Sentry workflow.

## Implemented Seer surface

| Contract | Status |
| --- | --- |
| `GET /v1/models` | Implemented |
| `POST /v1/automation/agent/feature/run` | Implemented (`autofix`, `autofix_single_run`) |
| `POST /v1/automation/explorer/chat` | Implemented (Seer Agent) |
| `POST /v1/automation/explorer/state` | Implemented |
| `POST /v1/automation/explorer/update` | Implemented (`create_pr`) |
| `POST /v1/automation/explorer/repos` | Implemented |
| `POST /v1/automation/explorer/state/pr` | Implemented |
| `POST /v1/automation/explorer/runs/by-ids` | Implemented |
| `POST /v1/automation/explorer/update` | `create_pr`, `create_branch`, `select_solution`, `user_input_response`, `interrupt` |
| `POST /v1/automation/summarize/issue` | Implemented |
| `POST /v1/automation/summarize/fixability` | Implemented |
| `POST /v1/llm/generate` | Implemented |
| `POST /v1/automation/oneshot/run` | `issue_view_title`, `chat_suggestions`, `agent_question`, `inbound_filter_name`, `autofix_issue_data_judge` |
| `POST /v1/automation/coding-agent/state/update` | Implemented |
| `POST /v1/automation/coding-agent/state/set` | Implemented |
| `POST /v1/pr-metrics/delegated-agent-match` | Implemented |
| `POST /v1/assisted-query/start`, `/state`, `/translate-agentic` | Implemented with heuristic/LLM query translation |
| `POST /v1/workflows/compare/cohort` | Implemented (cohort statistics) |
| `POST /v1/explorer/service-map/update` | Accepted; service-map storage is not persisted yet |
| `POST /v0/issues/supergroups/cluster-lightweight`, `/get`, `/get-by-group-ids` | Implemented with local supergroup clustering |
| `POST /v0/issues/similar-issues` | Implemented with local stack-trace similarity |
| `GET /v0/issues/similar-issues/grouping-record/delete/{project_id}` | Implemented |
| `/v1/anomaly-detection/detect`, `/store`, `/delete-alert-data`, `/alert-data` | Implemented with robust z-score detection |
| `/v1/code_review`, `/check/rerun`, `/review-request`, `/pr-closed` | Check run/commit status, inline review comments, annotations, rerun, and PR-closed cancellation |
| `/v1/offboarding/repository` | Accepted |
| Explorer indexing / export endpoints | Indexing accepted as no-ops; export returns the expected envelope |
| Seer → Sentry RPC | Implemented for issue/event/repo/committer/ownership/search/trace/log/metric/replay/profile/dsn/baseline/completion-hook calls |

Autofix produces the same block and artifact shapes Sentry's drawer expects:
`root_cause`, `solution`, `merged_file_patches`, `repo_pr_states`, and the
completion-hook callback that lets Sentry advance the step-by-step pipeline.
Multi-repository fixes use one `patches` entry per repo, PR iteration reuses the
existing branch/PR, and pushed commit SHAs are recorded on blocks so Sentry can
reconcile milestones.

The Seer Agent path runs a bounded model-driven tool loop: it can search Sentry
issues, load events and ownership, search/read repository code, inspect traces,
logs, metrics, replays, DSNs, and baseline tag distributions, and call
user-supplied custom tools. Tool calls and results are written into the run state
as `tool_use` blocks, which Sentry's Explorer UI can render. The deterministic
demo provider keeps the one-shot retrieval path for air-gapped use.

See [docs/parity.md](docs/parity.md) for the full endpoint and capability
matrix, [docs/seer-compatibility.md](docs/seer-compatibility.md) for wire shapes,
and [docs/code-review.md](docs/code-review.md) for the review loop.

## Quick start

```bash
cp .env.example .env
# Generate two strong shared secrets and set them in .env.
# Optional: set ANTHROPIC_API_KEY (default: Claude Opus 5.5).
# Demo mode works without a model key.

uv sync --extra dev
uv run tweaker seed-demo
uv run tweaker index-repos        # optional: build the local code index
uv run tweaker serve --reload
```

The console is at <http://localhost:8080>. To exercise the Seer contract without
a Sentry installation, use the demo console or run the compatibility tests.

### Docker Compose

```bash
cp .env.example .env
# Set TWEAKER_SECRET_KEY, SEER_API_SHARED_SECRET, SEER_RPC_SHARED_SECRET.
docker compose up --build
```

Services: `api` (8080), `worker`, `postgres`, `redis`. The web console is built
into the API image. If Sentry runs in another Compose project, attach Tweaker to
that network:

```bash
SENTRY_NETWORK=sentry_default SENTRY_NETWORK_EXTERNAL=true docker compose up
```

## Connect self-hosted Sentry

1. Set the shared secrets on **both** sides. Sentry and Tweaker must agree:
   - `SEER_API_SHARED_SECRET` — signs Sentry → Seer requests and the
     `X-Viewer-Context` JWT.
   - `SEER_RPC_SHARED_SECRET` — signs Seer → Sentry RPC requests. Sentry stores
     this as a list of keys during rotation; use the same single key in Tweaker
     or set the environment variable to that key.
2. In Sentry's configuration (for a Docker Compose install, usually
   `sentry/config.yml` or the corresponding environment overrides), set:

   ```yaml
   SEER_AUTOFIX_URL: "http://tweaker:8080"
   SEER_SUMMARIZATION_URL: "http://tweaker:8080"
   SEER_DEFAULT_TIMEOUT: 30
   SEER_API_SHARED_SECRET: "<same value as Tweaker>"
   SEER_RPC_SHARED_SECRET: "<same value as Tweaker>"
   ```

   If Sentry runs outside Tweaker's Compose network, use a reachable URL.
3. Set `TWEAKER_SENTRY_URL` to Sentry's internal API base, e.g.
   `http://sentry:9000`. This is used for Seer → Sentry RPC callbacks.
4. Ensure the Sentry organization has the Seer/Autofix features enabled. The
   exact flag names vary by Sentry release; common ones are
   `organizations:seer-added`, `organizations:autofix`, and
   `organizations:seer-explorer`.
5. Connect a source-control integration in Sentry and link repositories to the
   project. Tweaker reads the project's Seer repository preferences through the
   Sentry RPC and clones the repository using:
   - `TWEAKER_GITHUB_TOKEN` / `TWEAKER_GITLAB_TOKEN`, or
   - `TWEAKER_REPO_MAP` for an existing local checkout.
6. Open an issue in Sentry and use the existing Seer/Autofix controls.

> The Seer contract is internal and changes between Sentry releases. Pin a
> Sentry version, run the compatibility tests, and treat upgrades as integration
> changes. See [docs/seer-compatibility.md](docs/seer-compatibility.md).

## Source-control access

Tweaker clones code through the same repository coordinates Sentry sends
(`provider`, `owner`, `name`, `branch`, `base_commit_sha`, code mappings).

- **GitHub / GitLab token** — set `TWEAKER_GITHUB_TOKEN` or
  `TWEAKER_GITLAB_TOKEN` for private repositories and PR/MR creation.
- **GitHub Enterprise / self-hosted GitLab** — set
  `TWEAKER_GITHUB_ENTERPRISE_URL` / `TWEAKER_GITLAB_SELF_HOSTED_URL` and the
  matching `*_TOKEN` variables. Base URLs may include a port or path prefix.
- **Local checkout map** — for air-gapped installs or monorepos:

  ```bash
  TWEAKER_REPO_MAP='{"acme/store": "/srv/checkouts/store"}'
  ```

- **PR creation** — Autofix code changes are validated with `git apply --check`.
  `POST /v1/automation/explorer/update` with `type: "create_pr"` pushes a branch
  and opens a GitHub PR or GitLab MR. Tweaker never merges automatically.

## Models

The default is Claude Opus 5.5 through the official Anthropic SDK. Other options:

```bash
# Anthropic
ANTHROPIC_API_KEY=...
TWEAKER_ANTHROPIC_MODEL=claude-opus-5-5

# OpenAI-compatible (vLLM, LM Studio, Ollama, gateways)
TWEAKER_AI_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5-coder:32b

# Deterministic local analyzer (no model calls)
TWEAKER_AI_PROVIDER=demo
```

Telemetry is redacted recursively before it reaches a model. The deterministic
analyzer keeps demos and air-gapped installs usable without credentials.

## Operator console

The bundled React console is optional. It shows:

- Seer contract health and configured model.
- Recent Seer runs and their blocks/artifacts.
- The mirrored Sentry issues the legacy local workflow can inspect.
- Connection and automation settings.

Sentry's own issue page remains the primary UI for Seer workflows.

## Operations

- **Code index** — `tweaker index-repos` or `POST /api/repos/{id}/index` builds a
  local keyword/symbol index. The agent uses it as a fallback when `git grep`
  finds nothing, and it is useful for large or shallow checkouts.
- **Usage and quotas** — every model call is recorded. `GET /api/usage?org_id=`
  returns tokens, cost, model calls, runs, and the active quota.
  `PUT /api/usage/quota/{org_id}` sets monthly token, cost, and run limits.
  Exhausted budgets return `402` from Seer run endpoints.
- **Metrics** — `GET /api/metrics` returns Prometheus text metrics for runs,
  durations, and token usage. Protect it with `TWEAKER_AUTH_TOKEN`.
- **Rate limiting** — per-IP Seer API limits default to 600 requests/minute. The
  limiter is per process; use a shared gateway if you run multiple API replicas.
- **Request limits** — bodies above `TWEAKER_MAX_REQUEST_BYTES` are rejected.
- **Retries** — Sentry RPC calls retry connection errors, 429, and 5xx responses.

## MCP

```bash
pip install 'tweaker[mcp]'
TWEAKER_URL=http://localhost:8080 TWEAKER_AUTH_TOKEN=... tweaker-mcp
```

The MCP server exposes issue lookup, investigations, Seer-contract runs, Ask
Seer, patches, and usage. It uses the operator API, not the Seer HMAC contract.

## Configuration

All Tweaker settings use the `TWEAKER_` prefix; standard `SEER_*`,
`ANTHROPIC_API_KEY`, `OPENAI_*`, `GITHUB_TOKEN`, and `GITLAB_TOKEN` variables are
also accepted where noted. See [`.env.example`](.env.example).

| Variable | Purpose |
| --- | --- |
| `TWEAKER_SENTRY_URL` | Sentry internal API base for RPC callbacks. |
| `SEER_API_SHARED_SECRET` | Sentry → Tweaker HMAC and viewer-context JWT secret. |
| `SEER_RPC_SHARED_SECRET` | Tweaker → Sentry RPC HMAC secret. |
| `TWEAKER_SECRET_KEY` | Encrypts console integration credentials at rest. |
| `TWEAKER_DATABASE_URL` | SQLAlchemy URL. SQLite by default; Postgres in Compose. |
| `TWEAKER_RUNNER` | `inline` (dev) or `arq` (production). |
| `TWEAKER_REPO_MAP` | JSON map of `owner/name` to local checkout paths. |
| `TWEAKER_SEER_RATE_LIMIT_PER_MINUTE` | Per-IP Seer API limit; `0` disables. |
| `TWEAKER_MAX_REQUEST_BYTES` | Maximum request body size. |
| `TWEAKER_GITHUB_TOKEN` / `TWEAKER_GITLAB_TOKEN` | Cloud clone and PR/MR credentials. |
| `TWEAKER_GITHUB_ENTERPRISE_URL` / `*_TOKEN` | GitHub Enterprise base URL and token. |
| `TWEAKER_GITLAB_SELF_HOSTED_URL` / `*_TOKEN` | Self-hosted GitLab base URL and token. |
| `ANTHROPIC_API_KEY` | Enables Claude. Default model: `claude-opus-5-5`. |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI-compatible endpoint. |
| `TWEAKER_AI_PROVIDER` | `auto`, `anthropic`, `openai`, `demo`, or `none`. |
| `TWEAKER_AGENT_TOOL_LOOP` | Enable model-driven agent tool calling; disable for endpoints without tool support. |
| `/api/usage` | Per-org token/cost/run usage and quotas. |
| `/api/metrics` | Prometheus text metrics (bearer token protected). |

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
cd web && npm install && npm run dev   # Vite proxies /api and /v1 to :8080
```

Compatibility tests cover signed requests, viewer-context JWTs, the step-by-step
Autofix pipeline, the single-run pipeline, patch shapes, and issue summaries.

## Security model

- Sentry → Tweaker requests are HMAC-SHA256 signed with
  `SEER_API_SHARED_SECRET`; Tweaker verifies the raw body before parsing.
- `X-Viewer-Context` JWTs are verified (HS256, issuer, expiry, `kid`) and used for
  org/user attribution and outbound RPC callbacks.
- Tweaker → Sentry RPC calls are HMAC-signed with `SEER_RPC_SHARED_SECRET` and
  echo the viewer context.
- Telemetry is redacted recursively before model calls.
- Repository reads are constrained to the checkout root. Patches reject absolute
  paths, traversal, `.git`, binary changes, deletions, and symlinks, and are
  checked with `git apply --check` before publication.
- Generated patches are never merged automatically.

See [SECURITY.md](SECURITY.md).

## Limitations

Tweaker implements the complete Seer HTTP surface exercised by Sentry's
open-source client, but some capabilities are local heuristics or accepted
stubs rather than Seer's production models:

- No billing/credits/Seer Wallet.
- Anomaly detection, similarity, and supergroups use local statistical/text
  heuristics, not Seer's trained embedding models.
- Service maps are accepted but not persisted.
- Datadog/GCP/Grafana provider metadata is passed to the agent, but Tweaker does
  not call those services.
- Code review posts a check run/commit status and inline review comments; it does not reproduce every Seer review heuristic.
- Automatic PR iteration driven by CI/review comments is partial: feedback is
  applied to the existing branch/PR, but Sentry's full reaction loop is not
  replicated.
- The full Seer Agent surface (dashboards, monitors, issue mutation, code mode,
  interactive approvals) is not implemented. Custom tools are supported when
  the model selects one.

The operator console is not a replacement for Sentry's UI. Test against the
exact Sentry release you run.

## License

Apache-2.0. See [LICENSE](LICENSE).
