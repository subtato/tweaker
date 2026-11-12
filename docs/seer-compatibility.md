# Seer compatibility contract

This document describes the subset of Sentry's internal Seer service contract that
Tweaker implements. The contract is not public API. Pin the Sentry release you
run and re-run `tests/test_seer_compat.py` after upgrading.

## Authentication

Sentry → Tweaker:

- `Authorization: Rpcsignature rpc0:<hex>` where `<hex>` is
  `HMAC-SHA256(SEER_API_SHARED_SECRET, raw_request_body)`.
- `X-Viewer-Context`: an HS256 JWT signed with `SEER_API_SHARED_SECRET`.
  Claims include `version: 2`, `organization_id`, and `actor`
  (`{type, id, delegated_user_id?}`). Tweaker verifies the signature, issuer
  (`sentry`), expiry, and `kid`.

Tweaker → Sentry:

- `POST {TWEAKER_SENTRY_URL}/api/0/seer-rpc/{method}/`
- Body: `{"args": {...}}`
- `Authorization: Rpcsignature rpc0:<hex>` signed with
  `SEER_RPC_SHARED_SECRET`.
- `X-Viewer-Context` echoes the inbound JWT so Sentry can attribute the call.

## Endpoints

### `GET /v1/models`

Returns `{"models": ["<model>"]}`. Sentry calls this with an empty signed body.

### `POST /v1/automation/agent/feature/run`

Request:

```json
{
  "feature_id": "autofix",
  "payload": {
    "group_id": 1001,
    "project_id": 2,
    "short_id": "BACKEND-1A2B",
    "title": "KeyError: 'SPRING25'",
    "culprit": "app.checkout.apply_discount",
    "step": "root_cause",
    "step_args": {},
    "on_completion_hook": {
      "module_path": "sentry.seer.autofix.on_completion_hook.AutofixOnCompletionHook",
      "call_on_failure": true
    },
    "stopping_point": "root_cause"
  },
  "referrer": "api.web",
  "ref": "<sentry-run-uuid>",
  "external_idempotency_key": "<sentry-run-uuid>"
}
```

Response: `{"run_id": 123}`.

`feature_id` values handled:

- `autofix` / legacy `autofix_rca`: step-by-step. `step` is one of
  `root_cause`, `solution`, `code_changes`, `pr_iteration`.
- `autofix_single_run`: one run produces root cause, solution, code changes, and
  optionally a PR according to `stopping_point`.

After a step completes, Tweaker calls Sentry's `call_on_completion_hook` RPC.
Sentry's hook fetches the run state and advances the pipeline. For single runs,
the hook records milestones and does not start another step.

### `POST /v1/automation/explorer/chat`

Seer Agent request. Tweaker creates an Explorer run, gathers issue context when
the payload identifies one, answers with the configured model, and stores a user
block plus an assistant block.

### `POST /v1/automation/explorer/state`

Request: `{"run_id": 123, "organization_id": 1}`

Response:

```json
{
  "session": {
    "run_id": 123,
    "blocks": [],
    "status": "completed",
    "failure_reason": null,
    "updated_at": "2026-11-11T20:00:00Z",
    "repo_pr_states": {},
    "coding_agents": {},
    "queued_feedback": []
  },
  "feature_id": "autofix",
  "sentry_run_id": "<sentry-run-uuid>"
}
```

### `POST /v1/automation/explorer/update`

`{"run_id": 123, "organization_id": 1, "payload": {"type": "create_pr", ...}}`

Returns `202`. Tweaker pushes a branch and opens a GitHub PR or GitLab MR, then
writes `repo_pr_states` for the repo.

### `POST /v1/automation/explorer/repos`

Returns the project's Seer repository preferences in the shape Sentry's Autofix
repo picker expects.

### `POST /v1/automation/explorer/state/pr`

Looks up a run by PR id/URL and returns its state, or `{"session": null}`.

### `POST /v1/automation/explorer/runs/by-ids`

Returns `{"data": {"<run_id>": {"status": "completed", "run_id": 123}}}` for
batch status checks.

### `POST /v1/automation/summarize/issue`

Returns the issue summary shape:

```json
{
  "group_id": "1001",
  "headline": "...",
  "whats_wrong": "...",
  "trace": "KeyError: 'SPRING25'",
  "possible_cause": "...",
  "scores": {
    "possible_cause_confidence": 0.93,
    "possible_cause_novelty": 0.5,
    "fixability_score": 0.92
  }
}
```

### `POST /v1/automation/summarize/fixability`

Returns the same shape with the deterministic fixability score.

### `POST /v1/llm/generate`

Returns `{"content": "...", "response": "...", "usage": {...}}`.

### `POST /v1/automation/oneshot/run`

Request: `{"oneshot_id": "...", "payload": {...}}`

Response: `{"result": {...}}` — the envelope is important; Sentry's
`run_oneshot()` returns `data.get("result")`.

Implemented one-shots:

| `oneshot_id` | Result |
| --- | --- |
| `issue_view_title` | `{"title": "..."}` |
| `chat_suggestions` | `{"suggestions": [{"text", "kind", "action_type?"}]}` |
| `agent_question` | `{"answer": "..."}` |
| `inbound_filter_name` | `{"name": "..."}` |
| `autofix_issue_data_judge` | `{"verdict", "confidence", "reason", "model"}` |

## Agent tool blocks

`explorer/chat` performs grounded retrieval and writes tool activity into the
run state:

```json
{
  "id": "uuid",
  "message": {
    "role": "tool_use",
    "content": "",
    "metadata": {"step": "chat"},
    "tool_calls": [
      {"id": "search_issues", "function": "search_issues", "args": "{\"query\": \"...\"}"}
    ]
  },
  "tool_results": [
    {
      "tool_call_id": "search_issues",
      "tool_call_function": "search_issues",
      "content": "[...]"
    }
  ]
}
```

The final assistant block contains the answer. This mirrors the block shape
Sentry's Explorer UI already renders.

## Additional contract endpoints

| Endpoint | Behavior |
| --- | --- |
| `POST /v0/issues/supergroups/cluster-lightweight` | Groups by exception type + code area and stores a local supergroup |
| `POST /v0/issues/supergroups/get` | Returns `SupergroupDetailData` |
| `POST /v0/issues/supergroups/get-by-group-ids` | Returns `{"data": [...]}` |
| `POST /v0/issues/similar-issues` | Stack-trace token similarity; stores grouping records |
| `GET /v0/issues/similar-issues/grouping-record/delete/{project_id}` | Deletes grouping records for a project |
| `POST /v1/assisted-query/translate-agentic` | Natural language → Sentry search query |
| `POST /v1/assisted-query/start` | Creates an async assisted-query run and returns `run_id` |
| `POST /v1/assisted-query/state` | Returns `{"session": {run_id, status, current_step, completed_steps, final_response, unsupported_reason}}` |
| `POST /v1/workflows/compare/cohort` | Cohort counts and numeric summaries |
| `POST /v1/explorer/service-map/update` | Accepted; returns 202 |
| `POST /v1/automation/coding-agent/state/update` | Updates a coding-agent entry on a run |
| `POST /v1/automation/coding-agent/state/set` | Replaces coding-agent entries on a run |
| `POST /v1/pr-metrics/delegated-agent-match` | Matches a branch/PR URL to a run; 202 when unmatched |
| `POST /v1/anomaly-detection/detect` | Robust median/MAD anomaly flags |
| `POST /v1/anomaly-detection/store` | Accepted; returns `{"success": true}` |
| `POST /v1/anomaly-detection/delete-alert-data` | Accepted; returns `{"success": true}` |
| `POST /v1/anomaly-detection/alert-data` | Empty threshold-data envelope |
| `POST /v1/code_review`, `/check/rerun`, `/review-request` | Creates a check run/commit status, posts inline review comments and annotations, and completes the check |
| `POST /v1/code_review/pr-closed` | Cancels the matching check run |
| `POST /v1/offboarding/repository` | Accepted |

## Blocks and artifacts

Autofix blocks use the shape Sentry's `SeerRunState` and `ExplorerAutofixState`
expect:

```json
{
  "id": "uuid",
  "message": {
    "role": "assistant",
    "content": "One-line summary",
    "metadata": {"step": "root_cause", "referrer": "api.web"}
  },
  "timestamp": "2026-11-11T20:00:00Z",
  "loading": false,
  "artifacts": [
    {
      "key": "root_cause",
      "data": {
        "one_line_description": "...",
        "five_whys": ["..."],
        "reproduction_steps": ["..."],
        "cause": "..."
      },
      "reason": "..."
    }
  ]
}
```

Solution artifacts use `{"one_line_summary", "steps": [{"title", "description"}]}`.

Code changes use `merged_file_patches` with the `AgentFilePatch` shape:

```json
{
  "repo_name": "acme/store",
  "diff": "--- a/app/checkout.py\n+++ b/app/checkout.py\n...",
  "code_url": null,
  "patch": {
    "path": "app/checkout.py",
    "type": "M",
    "added": 2,
    "removed": 1,
    "source_file": "app/checkout.py",
    "target_file": "app/checkout.py",
    "hunks": [
      {
        "source_start": 1,
        "source_length": 8,
        "target_start": 1,
        "target_length": 9,
        "section_header": "",
        "lines": [
          {
            "diff_line_no": 1,
            "source_line_no": 1,
            "target_line_no": 1,
            "line_type": " ",
            "value": "DISCOUNTS = {"
          }
        ]
      }
    ]
  }
}
```

PR states use `{"repo_name", "provider", "branch_name", "pr_number", "pr_url",
"commit_sha", "pr_creation_status", "pr_creation_error"}`.

When a fix spans multiple repositories, the model returns one `patches` entry per
repository and Tweaker writes one `AgentFilePatch` per repo. Each repo gets its
own PR state.

For `pr_iteration`, Tweaker checks out the existing PR branch, includes the
feedback text in the diagnosis prompt, validates the new diff against that
branch, and reuses the existing PR URL when pushing. Successful pushes add a
`pr_commit_shas` map to the code-change block, which Sentry uses for milestone
reconciliation and push de-duplication.

## Sentry RPC methods used

| Method | Purpose |
| --- | --- |
| `get_issue_details` | Issue metadata, project, tags, activity |
| `get_latest_issue_event` | Full latest event with stack frames |
| `get_error_event_details` | Event by project/event id |
| `get_issue_committers` | Suspect/stack/release commits |
| `get_issue_ownership` | Code owners for failing files |
| `execute_issues_query` | Issue search for grounded agent answers |
| `get_event_details` | Event details by issue or event id |
| `get_trace_waterfall` | Trace spans and connected errors |
| `get_issue_filter_keys` | Searchable issue fields |
| `get_filter_key_values` | Values for a searchable issue field |
| `get_attribute_names` | Trace/log/metric attribute names |
| `get_attribute_values_with_substring` | Attribute value lookup |
| `get_project_preferences` | Seer repository definitions |
| `get_repository_definition` | Repository lookup by owner/name |
| `get_organization_projects` | Org project list |
| `get_organization_slug` | Org slug |
| `get_commit_author` | Viewer GitHub identity for PR attribution |
| `call_on_completion_hook` | Advance the Autofix pipeline |

## Sentry feature flags

Sentry gates the Seer UI. Flag names change between releases. On the Sentry side,
look for flags in `src/sentry/seer` and `src/sentry/features`. Common flags at the
time of writing include `organizations:seer-added`, `organizations:autofix`, and
`organizations:seer-explorer`. Self-hosted Sentry also has
`get_seer_automation_ineligibility_reason()` returning
`not_eligible.gen_ai_feature_disabled` for self-hosted; this is a Sentry-side gate
you may need to patch or feature-flag for your deployment.

## Not implemented

- Billing, credits, quotas, and the Seer Wallet.
- Full production-grade anomaly detection, embedding-based grouping, and
  persisted service maps. Tweaker provides local heuristics and accepted
  envelopes, not Seer's trained models.
- Datadog/GCP/Grafana monitoring-provider connectors. Provider metadata is
  passed into agent context, but Tweaker does not call those services.
- Automatic PR iteration driven by CI/review comments. The `pr_iteration` step
  accepts feedback and updates the existing branch/PR, but Sentry's full
  check-suite/review reaction loop is not replicated.
- Every Seer code-review heuristic. The check-run/review-comment loop is
  implemented; the model review quality is our own.
- Coding-agent handoff (Cursor/Claude/Copilot) beyond returning an accepted
  response.
- The full Seer Agent tool surface: dashboards, monitors, issue mutation,
  code-mode execution, interactive approvals, and some trace/log/profile tools.
  Custom tools are supported through `call_custom_tool` when the model selects
  one.
- Sentry's `send_seer_webhook` and some admin-only RPC methods.
