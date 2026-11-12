# Seer parity matrix

Tweaker targets the Seer contract exercised by the open-source Sentry client
(`src/sentry/seer/signed_seer_api.py`, `agent/client_utils.py`, and
`autofix/utils.py`). The HTTP surface is complete; capability depth varies.

## HTTP contract

| Endpoint | Status | Notes |
| --- | --- | --- |
| `GET /v1/models` | Full | |
| `POST /v1/automation/agent/feature/run` | Full | `autofix`, `autofix_rca`, `autofix_single_run` |
| `POST /v1/automation/explorer/chat` | Full | Bounded model-driven tool loop, custom tools, artifacts |
| `POST /v1/automation/explorer/state` | Full | |
| `POST /v1/automation/explorer/update` | Full | `create_pr`, `create_branch`, `select_solution`, `user_input_response`, `interrupt` |
| `POST /v1/automation/explorer/repos` | Full | |
| `POST /v1/automation/explorer/state/pr` | Full | |
| `POST /v1/automation/explorer/runs/by-ids` | Full | |
| `POST /v1/automation/summarize/issue` | Full | |
| `POST /v1/automation/summarize/fixability` | Full | Fast local classifier |
| `POST /v1/llm/generate` | Full | |
| `POST /v1/automation/oneshot/run` | Full | 5 one-shots |
| `POST /v1/automation/coding-agent/state/update` | Full | |
| `POST /v1/automation/coding-agent/state/set` | Full | |
| `POST /v1/pr-metrics/delegated-agent-match` | Full | |
| `POST /v1/automation/explorer/index` | Accepted | Reads code on demand |
| `POST /v1/automation/explorer/index/org-repo-knowledge` | Accepted | |
| `POST /v1/automation/explorer/index/org-project-knowledge` | Accepted | |
| `POST /v1/automation/explorer/index/sentry-knowledge` | Accepted | |
| `POST /v1/automation/explorer/export-indexes` | Full | Expected envelope |
| `POST /v1/assisted-query/start` | Full | Async run |
| `POST /v1/assisted-query/state` | Full | |
| `POST /v1/assisted-query/translate-agentic` | Full | LLM + heuristic fallback |
| `POST /v1/workflows/compare/cohort` | Full | Numeric summaries |
| `POST /v1/explorer/service-map/update` | Accepted | Not persisted |
| `POST /v0/issues/supergroups/cluster-lightweight` | Heuristic | Exception + code-area grouping |
| `POST /v0/issues/supergroups/get` | Full | |
| `POST /v0/issues/supergroups/get-by-group-ids` | Full | |
| `POST /v0/issues/similar-issues` | Heuristic | Stack-trace token similarity |
| `GET /v0/issues/similar-issues/grouping-record/delete/{project_id}` | Full | |
| `POST /v1/anomaly-detection/detect` | Heuristic | Median/MAD robust z-score |
| `POST /v1/anomaly-detection/store` | Accepted | |
| `POST /v1/anomaly-detection/delete-alert-data` | Accepted | |
| `POST /v1/anomaly-detection/alert-data` | Accepted | Empty envelope |
| `POST /v1/code_review` | Full loop | Check run, inline comments, annotations |
| `POST /v1/code_review/check/rerun` | Full loop | New check run per rerun |
| `POST /v1/code_review/review-request` | Full loop | Check run + review comments |
| `POST /v1/code_review/pr-closed` | Full loop | Cancels matching check runs |
| `POST /v1/offboarding/repository` | Accepted | |

## Sentry RPC methods

Implemented in `src/tweaker/seer/rpc.py`:

- `get_issue_details`, `get_latest_issue_event`, `get_error_event_details`
- `get_event_details`, `execute_issues_query`
- `get_issue_committers`, `get_issue_ownership`, `get_group_assignees`
- `get_team_members`, `get_project_members`
- `get_project_preferences`, `get_repository_definition`
- `get_organization_projects`, `get_organization_slug`
- `get_trace_waterfall`, `get_profile_flamegraph`
- `get_log_attributes_for_trace`, `get_metric_attributes_for_trace`
- `get_metric_metadata`, `get_attribute_names`,
  `get_attribute_values_with_substring`
- `get_issue_filter_keys`, `get_filter_key_values`
- `get_replay_metadata`, `get_replay_summary_logs`
- `get_dsn`, `get_baseline_tag_distribution`
- `call_custom_tool`, `call_on_completion_hook`, `deliver_feature_result`

## Production features

- Local code index (`/api/repos/{id}/index`, `tweaker index-repos`).
- Per-org usage records and monthly token/cost/run quotas (`/api/usage`).
- Per-IP Seer API rate limiting.
- Prometheus metrics at `/api/metrics`.
- Request body size limits.
- Retrying Sentry RPC client.

## Capability gaps

These are deliberate local implementations or accepted stubs, not missing HTTP
routes:

- Billing, credits, quotas, Seer Wallet.
- Trained embedding models for similarity/grouping/supergroups.
- Production anomaly detection model.
- Persisted service maps.
- Datadog/GCP/Grafana data connectors.
- Every Seer code-review heuristic; the check-run/review-comment loop is implemented.
- Full CI/review-driven PR iteration.
- Coding-agent handoff execution.
- Full agent actions (dashboards, monitors, issue mutation, code mode,
  interactive approvals).
