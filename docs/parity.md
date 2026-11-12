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
| `POST /v1/automation/explorer/index` | Full | Indexes org/project repositories |
| `POST /v1/automation/explorer/index/org-repo-knowledge` | Full | Indexes org/project repositories |
| `POST /v1/automation/explorer/index/org-project-knowledge` | Accepted | |
| `POST /v1/automation/explorer/index/sentry-knowledge` | Accepted | |
| `POST /v1/automation/explorer/export-indexes` | Full | Exports indexed files per repo |
| `POST /v1/assisted-query/start` | Full | Async run |
| `POST /v1/assisted-query/state` | Full | |
| `POST /v1/assisted-query/translate-agentic` | Full | LLM + heuristic fallback |
| `POST /v1/workflows/compare/cohort` | Full | Mean/stdev, delta, relative change, z-score, significance |
| `POST /v1/explorer/service-map/update` | Full | Persisted per organization |
| `POST /v0/issues/supergroups/cluster-lightweight` | Heuristic | Exception + code-area grouping |
| `POST /v0/issues/supergroups/get` | Full | |
| `POST /v0/issues/supergroups/get-by-group-ids` | Full | |
| `POST /v0/issues/similar-issues` | Heuristic | Stack-trace token similarity |
| `GET /v0/issues/similar-issues/grouping-record/delete/{project_id}` | Full | |
| `POST /v1/anomaly-detection/detect` | Heuristic | Median/MAD robust z-score; honors sensitivity and direction |
| `POST /v1/anomaly-detection/store` | Full | Persists per-detector config and history |
| `POST /v1/anomaly-detection/delete-alert-data` | Full | Deletes the detector's stored history |
| `POST /v1/anomaly-detection/alert-data` | Full | Returns stored threshold points for a time range |
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
- `notify_seer_pr_created` (PR attribution and run linking)
- `send_seer_webhook` (issue-stream lifecycle activities)
- `get_organization_features`, `get_issues_stats`
- `get_event_filter_keys`, `get_event_filter_key_values`
- `get_profile_details`, `get_attributes_for_span`
- `get_transactions_for_project`, `get_trace_for_transaction`,
  `get_profiles_for_trace`, `get_issues_for_transaction`
- `execute_table_query`, `execute_timeseries_query`,
  `execute_trace_table_query`
- `get_issues_by_raw_query`, `get_issues_by_function_name`,
  `get_issues_related_to_exception_type`
- `find_referenced_fix_statements`, `has_repo_code_mappings`,
  `get_repo_installation_id`

## Seer lifecycle webhooks

Tweaker emits the same `seer.*` events Sentry's open-source handler understands,
best-effort and without failing the run when Sentry is unavailable:

- `root_cause_started`, `root_cause_completed`
- `solution_started`, `solution_completed`
- `coding_started`, `coding_completed`
- `pr_created`, `pr_ready_for_review`
- `iteration_started`, `iteration_completed`
- `errored`, `stopped`

Payloads include `run_id`, `group_id`, and the relevant `root_cause`,
`solution`, `pull_requests`, `code_changes`, or `stop` object. Sentry uses these
to write issue-stream activities, notify Sentry Apps, and drive autofix
continuation.

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

- Billing, credits, and Seer Wallet. Local token/cost/run quotas are implemented.
- Trained embedding models for similarity/grouping/supergroups.
- Production anomaly detection model (Tweaker persists history and serves
  threshold data, but detection is a robust z-score).
- Datadog/GCP/Grafana data connectors.
- Every Seer code-review heuristic; the check-run/review-comment loop and
  `original_run_id` reruns are implemented.
- Full CI/review-driven PR iteration.
- Coding-agent handoff execution.
- Agent dashboards, monitors, issue mutation, and code-mode execution.
  Interactive `ask_user_question` approvals are implemented.
