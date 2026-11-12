from __future__ import annotations

import hashlib
import hmac
import json
import time

import jwt

from tweaker.seer.rpc import SentryRPCClient

SECRET = "test-seer-api-secret-0123456789abcdef"
ORG_ID = 4242
GROUP_ID = 1001


def _event() -> dict:
    return {
        "eventID": "evt1",
        "dateCreated": "2026-11-11T20:00:00Z",
        "title": "KeyError: 'SPRING25'",
        "message": "'SPRING25'",
        "culprit": "app.checkout.apply_discount",
        "platform": "python",
        "release": {"version": "store@1.8.2"},
        "tags": [
            {"key": "environment", "value": "production"},
            {"key": "release", "value": "store@1.8.2"},
        ],
        "entries": [
            {
                "type": "exception",
                "data": {
                    "values": [
                        {
                            "type": "KeyError",
                            "value": "'SPRING25'",
                            "stacktrace": {
                                "frames": [
                                    {
                                        "filename": "app/api.py",
                                        "function": "checkout",
                                        "lineNo": 4,
                                        "inApp": True,
                                        "contextLine": "return final_total(order, payload.get('coupon'))",
                                    },
                                    {
                                        "filename": "app/checkout.py",
                                        "function": "apply_discount",
                                        "lineNo": 7,
                                        "inApp": True,
                                        "contextLine": "discount = DISCOUNTS[code]",
                                        "preContext": [
                                            "DISCOUNTS = {",
                                            '    "WELCOME10": 0.10,',
                                            "}",
                                            "",
                                            "def apply_discount(order, code):",
                                        ],
                                        "postContext": ['    return order["total"] * (1 - discount)'],
                                    },
                                ]
                            },
                        }
                    ]
                },
            }
        ],
        "breadcrumbs": {"values": []},
        "contexts": {},
        "user": {},
        "request": {"url": "https://shop.test/api/checkout", "method": "POST"},
        "extra": {"coupon": "SPRING25"},
    }


def _fake_call(self, method: str, args: dict):
    if method == "get_issue_details":
        return {
            "issue": {
                "id": GROUP_ID,
                "shortId": "BACKEND-1A2B",
                "title": "KeyError: 'SPRING25'",
                "culprit": "app.checkout.apply_discount",
                "level": "error",
                "status": "unresolved",
                "platform": "python",
                "count": 1284,
                "firstSeen": "2026-11-09T00:00:00Z",
                "lastSeen": "2026-11-11T20:00:00Z",
                "permalink": "https://sentry.test/issues/1001/",
                "metadata": {"type": "KeyError", "value": "'SPRING25'"},
            },
            "project_id": 2,
            "project_slug": "backend",
        }
    if method == "get_latest_issue_event":
        return {"id": GROUP_ID, "title": "KeyError", "events": [_event()]}
    if method == "get_issue_committers":
        return {
            "suspect_commits": [
                {"id": "abc123", "message": "Add coupon campaign", "authorName": "Sam"}
            ],
            "stack_commits": [],
            "release_commits": [],
        }
    if method == "get_project_preferences":
        return {
            "organization_id": ORG_ID,
            "project_id": 2,
            "repositories": [
                {
                    "provider": "github",
                    "owner": "acme",
                    "name": "store",
                    "external_id": "42",
                    "branch_name": "main",
                    "code_mappings": [],
                }
            ],
        }
    if method == "get_issue_ownership":
        return {
            "owners": [
                {"type": "user", "name": "Sam", "username": "sam", "email": "sam@acme.test"}
            ],
            "matched_rules": ["app/checkout.py"],
            "auto_assignment": True,
            "project_id": 2,
            "project_slug": "backend",
        }
    if method == "execute_issues_query":
        return [
            {
                "id": "1001",
                "shortId": "BACKEND-1A2B",
                "title": "KeyError: 'SPRING25'",
                "culprit": "app.checkout.apply_discount",
                "count": 1284,
                "lastSeen": "2026-11-11T20:00:00Z",
                "permalink": "https://sentry.test/issues/1001/",
                "project": {"id": 2, "slug": "backend"},
            },
            {
                "id": "1002",
                "shortId": "FRONTEND-7C3D",
                "title": "TypeError in cart",
                "culprit": "cartTotal",
                "count": 386,
                "lastSeen": "2026-11-11T18:00:00Z",
                "permalink": "https://sentry.test/issues/1002/",
                "project": {"id": 3, "slug": "frontend"},
            },
        ]
    if method == "call_on_completion_hook":
        return None
    if method == "get_organization_slug":
        return {"slug": "acme"}
    return None


def _viewer_token() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "version": 2,
            "actor": {"type": "user", "id": 42},
            "organization_id": ORG_ID,
            "iat": now,
            "exp": now + 300,
            "iss": "sentry",
        },
        SECRET,
        algorithm="HS256",
        headers={"kid": hashlib.sha256(SECRET.encode()).hexdigest()[:8]},
    )


def _signed(payload: dict | None = None) -> tuple[bytes, dict[str, str]]:
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {
        "Authorization": f"Rpcsignature rpc0:{signature}",
        "Content-Type": "application/json",
        "X-Viewer-Context": _viewer_token(),
    }


def _wait(client, run_id: int, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body, headers = _signed({"run_id": run_id, "organization_id": ORG_ID})
        response = client.post("/v1/automation/explorer/state", content=body, headers=headers)
        assert response.status_code == 200
        session = response.json()["session"]
        if session["status"] in {"completed", "error"}:
            return session
        time.sleep(0.1)
    raise AssertionError("Seer run did not finish")


def test_models_requires_signature(client):
    assert client.get("/v1/models").status_code == 401
    _, headers = _signed()
    response = client.get("/v1/models", headers=headers)
    assert response.status_code == 200
    assert response.json()["models"]


def test_feature_run_root_cause_and_patch(client, monkeypatch):
    monkeypatch.setattr(SentryRPCClient, "call", _fake_call)
    ref = "11111111-1111-1111-1111-111111111111"

    root_payload = {
        "feature_id": "autofix",
        "payload": {
            "group_id": GROUP_ID,
            "project_id": 2,
            "short_id": "BACKEND-1A2B",
            "title": "KeyError: 'SPRING25'",
            "culprit": "app.checkout.apply_discount",
            "step": "root_cause",
            "on_completion_hook": {
                "module_path": "sentry.seer.autofix.on_completion_hook.AutofixOnCompletionHook"
            },
        },
        "referrer": "api.web",
        "ref": ref,
        "external_idempotency_key": ref,
    }
    body, headers = _signed(root_payload)
    response = client.post("/v1/automation/agent/feature/run", content=body, headers=headers)
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    session = _wait(client, run_id)
    assert session["status"] == "completed"
    artifacts = session["blocks"][0]["artifacts"]
    assert artifacts[0]["key"] == "root_cause"
    assert "SPRING25" in artifacts[0]["data"]["one_line_description"] or "coupon" in artifacts[0]["data"]["one_line_description"]

    code_payload = {
        **root_payload,
        "payload": {
            **root_payload["payload"],
            "step": "code_changes",
        },
    }
    body, headers = _signed(code_payload)
    response = client.post("/v1/automation/agent/feature/run", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
    session = _wait(client, run_id)
    assert session["status"] == "completed"
    code_block = session["blocks"][-1]
    assert code_block["merged_file_patches"]
    patch = code_block["merged_file_patches"][0]
    assert patch["patch"]["path"] == "app/checkout.py"
    assert patch["patch"]["added"] == 2
    assert patch["patch"]["removed"] == 1


def test_single_run_stops_at_code_changes(client, monkeypatch):
    monkeypatch.setattr(SentryRPCClient, "call", _fake_call)
    payload = {
        "feature_id": "autofix_single_run",
        "payload": {
            "group_id": GROUP_ID,
            "project_id": 2,
            "short_id": "BACKEND-1A2B",
            "title": "KeyError: 'SPRING25'",
            "culprit": "app.checkout.apply_discount",
            "stopping_point": "code_changes",
        },
        "referrer": "api.web",
        "ref": "22222222-2222-2222-2222-222222222222",
    }
    body, headers = _signed(payload)
    response = client.post("/v1/automation/agent/feature/run", content=body, headers=headers)
    assert response.status_code == 200
    session = _wait(client, response.json()["run_id"])
    assert session["status"] == "completed"
    steps = [block["message"]["metadata"]["step"] for block in session["blocks"]]
    assert steps == ["root_cause", "solution", "code_changes"]
    assert session["blocks"][0]["artifacts"][0]["key"] == "single_run_root_cause"
    assert session["blocks"][0]["artifacts"][0]["data"]["headline"]
    assert session["blocks"][-1]["merged_file_patches"]

    body, headers = _signed({"run_ids": [session["run_id"]]})
    response = client.post(
        "/v1/automation/explorer/runs/by-ids", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["data"][str(session["run_id"])]["status"] == "completed"


def test_explorer_chat_uses_issue_search_tools(client, monkeypatch):
    monkeypatch.setattr(SentryRPCClient, "call", _fake_call)
    payload = {
        "organization_id": ORG_ID,
        "query": "Which issue should on-call look at first?",
        "project_id": 2,
        "metadata": {"referrer": "api.web"},
        "user_org_context": {"org_slug": "acme"},
    }
    body, headers = _signed(payload)
    response = client.post("/v1/automation/explorer/chat", content=body, headers=headers)
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    session = _wait(client, run_id)
    assert session["status"] == "completed"
    roles = [block["message"]["role"] for block in session["blocks"]]
    assert roles[0] == "user"
    assert "tool_use" in roles
    assert roles[-1] == "assistant"
    tool_block = next(block for block in session["blocks"] if block["message"]["role"] == "tool_use")
    assert any(
        result["tool_call_function"] == "search_issues"
        for result in tool_block["tool_results"]
    )
    assert "BACKEND-1A2B" in session["blocks"][-1]["message"]["content"]


def test_oneshot_returns_result_envelope(client):
    body, headers = _signed(
        {
            "oneshot_id": "chat_suggestions",
            "payload": {
                "route": "/organizations/acme/issues/1001/",
                "page_context": "KeyError in checkout",
                "actions_enabled": True,
            },
        }
    )
    response = client.post("/v1/automation/oneshot/run", content=body, headers=headers)
    assert response.status_code == 200
    suggestions = response.json()["result"]["suggestions"]
    assert suggestions
    assert suggestions[0]["kind"] == "question"

    body, headers = _signed(
        {"oneshot_id": "issue_view_title", "payload": {"query": "unresolved checkout errors"}}
    )
    response = client.post("/v1/automation/oneshot/run", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["result"]["title"]


def test_fast_fixability_does_not_require_rpc(client, monkeypatch):
    def fail_call(self, method, args):
        raise AssertionError("fixability must not make RPC calls")

    monkeypatch.setattr(SentryRPCClient, "call", fail_call)
    body, headers = _signed(
        {
            "organization_id": ORG_ID,
            "group_id": GROUP_ID,
            "project_id": 2,
            "summary": {"whats_wrong": "None dereference", "trace": "TypeError"},
        }
    )
    response = client.post(
        "/v1/automation/summarize/fixability", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["scores"]["fixability_score"] == 0.68


def test_summarize_issue(client, monkeypatch):
    monkeypatch.setattr(SentryRPCClient, "call", _fake_call)
    body, headers = _signed(
        {"organization_id": ORG_ID, "group_id": GROUP_ID, "issue": {}, "project_id": 2}
    )
    response = client.post("/v1/automation/summarize/issue", content=body, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["group_id"] == str(GROUP_ID)
    assert data["headline"]
    assert data["scores"]["fixability_score"] > 0


def test_agent_question_oneshot_summarizes_run(client):
    from tweaker.db import session_scope
    from tweaker.models import SeerRun
    from tweaker.seer.state import block

    with session_scope() as session:
        run = SeerRun(
            org_id=ORG_ID,
            org_slug="acme",
            project_id=2,
            group_id=GROUP_ID,
            feature_id="autofix",
            kind="feature",
            status="completed",
            title="Autofix — BACKEND-1A2B",
            blocks=[
                block(
                    step="root_cause",
                    content="Unknown coupon code raises KeyError.",
                    artifacts=[
                        {
                            "key": "root_cause",
                            "data": {"one_line_description": "Unknown coupon code raises KeyError."},
                            "reason": "test",
                        }
                    ],
                )
            ],
            payload={},
            run_metadata={"feature_id": "autofix"},
            repo_pr_states={},
            coding_agents={},
            queued_feedback=[],
            usage={"usages": []},
            referrer="api.web",
            sentry_run_id=None,
            external_idempotency_key=None,
        )
        session.add(run)
        session.flush()
        run_id = run.id
    body, headers = _signed(
        {
            "oneshot_id": "agent_question",
            "payload": {"run_id": run_id, "question": "Summarize this run."},
        }
    )
    response = client.post("/v1/automation/oneshot/run", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["result"]["answer"]


def test_inbound_filter_name_oneshot(client):
    body, headers = _signed(
        {
            "oneshot_id": "inbound_filter_name",
            "payload": {
                "data_type": "error",
                "conditions": [{"key": "release", "value": "1.2.3"}],
            },
        }
    )
    response = client.post("/v1/automation/oneshot/run", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["result"]["name"]


def test_multi_repo_patch_pairs():
    from tweaker.seer.engine import _patch_pairs
    from tweaker.seer.repos import RepoDefinition
    from tweaker.services.llm.schema import Diagnosis, RepoPatch

    repos = [
        RepoDefinition(provider="github", owner="acme", name="store"),
        RepoDefinition(provider="github", owner="acme", name="web"),
    ]
    diagnosis = Diagnosis(
        summary="Two repos need a coordinated change.",
        root_cause="The contract changed.",
        confidence=0.8,
        patches=[
            RepoPatch(repo="acme/store", patch="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"),
            RepoPatch(repo="acme/web", patch="--- a/b.ts\n+++ b/b.ts\n@@ -1 +1 @@\n-x\n+y\n"),
        ],
    )
    pairs = _patch_pairs(diagnosis, repos)
    assert [repo.full_name for repo, _ in pairs] == ["acme/store", "acme/web"]


def test_pr_iteration_feedback_is_readable():
    from tweaker.seer.engine import _feedback_text

    payload = {
        "step_args": {
            "feedback": '[{"text": "Please handle expired coupons", "source": {"type": "github-pr-comment"}}]'
        }
    }
    text = _feedback_text(payload)
    assert "expired coupons" in text
    assert "github-pr-comment" in text


def test_remaining_seer_contract_endpoints(client):
    # Supergroups
    cluster_payload = {
        "organization_id": ORG_ID,
        "project_id": 2,
        "group_id": GROUP_ID,
        "issue": {
            "id": GROUP_ID,
            "title": "KeyError: 'SPRING25'",
            "metadata": {"type": "KeyError", "filename": "app/checkout.py"},
            "events": [_event()],
        },
    }
    body, headers = _signed(cluster_payload)
    response = client.post(
        "/v0/issues/supergroups/cluster-lightweight", content=body, headers=headers
    )
    assert response.status_code == 200
    supergroup_id = response.json()["supergroup_id"]

    body, headers = _signed(
        {"organization_id": ORG_ID, "supergroup_id": supergroup_id}
    )
    response = client.post("/v0/issues/supergroups/get", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["group_ids"] == [GROUP_ID]

    body, headers = _signed(
        {"organization_id": ORG_ID, "group_ids": [GROUP_ID]}
    )
    response = client.post(
        "/v0/issues/supergroups/get-by-group-ids", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["data"]

    # Index export
    body, headers = _signed({"org_id": ORG_ID})
    response = client.post(
        "/v1/automation/explorer/export-indexes", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["org_id"] == ORG_ID
    assert response.json()["version"] == 1

    # Service map and grouping cleanup are accepted.
    body, headers = _signed({"organization_id": ORG_ID, "nodes": [], "edges": []})
    response = client.post("/v1/explorer/service-map/update", content=body, headers=headers)
    assert response.status_code == 202
    body, headers = _signed()
    response = client.get(
        "/v0/issues/similar-issues/grouping-record/delete/2", headers=headers
    )
    assert response.status_code == 200

    # Assisted query translate/start/state
    body, headers = _signed(
        {
            "org_id": ORG_ID,
            "org_slug": "acme",
            "project_ids": [2],
            "natural_language_query": "show slow checkout traces",
            "strategy": "Traces",
        }
    )
    response = client.post(
        "/v1/assisted-query/translate-agentic", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["query"]

    body, headers = _signed(
        {
            "org_id": ORG_ID,
            "org_slug": "acme",
            "project_ids": [2],
            "natural_language_query": "show slow checkout traces",
            "strategy": "Traces",
        }
    )
    response = client.post("/v1/assisted-query/start", content=body, headers=headers)
    assert response.status_code == 200
    assisted_id = response.json()["run_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        body, headers = _signed({"run_id": assisted_id, "organization_id": ORG_ID})
        state = client.post(
            "/v1/assisted-query/state", content=body, headers=headers
        ).json()["session"]
        if state["status"] in {"completed", "error"}:
            break
        time.sleep(0.1)
    assert state["status"] == "completed"
    assert state["final_response"]["query"]

    # Cohort comparison
    body, headers = _signed(
        {
            "baseline": [{"duration": 10}, {"duration": 20}],
            "outliers": [{"duration": 100}],
            "total_baseline": 2,
            "total_outliers": 1,
            "config": {},
            "meta": {},
        }
    )
    response = client.post("/v1/workflows/compare/cohort", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["baseline"]["count"] == 2

    # Coding-agent state and delegated-agent matching
    from tweaker.db import session_scope
    from tweaker.models import SeerRun

    with session_scope() as session:
        run = SeerRun(
            org_id=ORG_ID,
            org_slug="acme",
            project_id=2,
            group_id=GROUP_ID,
            feature_id="autofix",
            kind="feature",
            status="completed",
            title="Coding agent run",
            blocks=[],
            payload={},
            run_metadata={},
            repo_pr_states={
                "acme/store": {
                    "repo_name": "acme/store",
                    "branch_name": "tweaker/fix-1",
                    "pr_url": "https://github.com/acme/store/pull/1",
                    "pr_id": "1",
                    "pr_creation_status": "completed",
                }
            },
            coding_agents={},
            queued_feedback=[],
            usage={"usages": []},
            referrer="api.web",
        )
        session.add(run)
        session.flush()
        coding_run_id = run.id

    body, headers = _signed(
        {
            "run_id": coding_run_id,
            "coding_agent_states": [
                {"id": "agent-1", "status": "running", "name": "Cursor"}
            ],
        }
    )
    response = client.post(
        "/v1/automation/coding-agent/state/set", content=body, headers=headers
    )
    assert response.status_code == 200

    body, headers = _signed(
        {"agent_id": "agent-1", "updates": {"status": "completed"}}
    )
    response = client.post(
        "/v1/automation/coding-agent/state/update", content=body, headers=headers
    )
    assert response.status_code == 200

    body, headers = _signed(
        {
            "organization_id": ORG_ID,
            "head_branch": "tweaker/fix-1",
            "pr_url": "https://github.com/acme/store/pull/1",
            "provider": "github",
            "group_ids": [GROUP_ID],
        }
    )
    response = client.post(
        "/v1/pr-metrics/delegated-agent-match", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["run_id"] == coding_run_id


def test_similar_issues_grouping(client):
    first = {
        "project_id": 2,
        "hash": "hash-a",
        "exception_type": "KeyError",
        "stacktrace": "app.checkout.apply_discount DISCOUNTS[code] KeyError",
        "k": 5,
        "threshold": 0.2,
    }
    body, headers = _signed(first)
    response = client.post("/v0/issues/similar-issues", content=body, headers=headers)
    assert response.status_code == 200

    second = {
        **first,
        "hash": "hash-b",
        "stacktrace": "app.checkout.apply_discount DISCOUNTS[code] KeyError",
    }
    body, headers = _signed(second)
    response = client.post("/v0/issues/similar-issues", content=body, headers=headers)
    assert response.status_code == 200
    responses = response.json()["responses"]
    assert any(item["parent_hash"] == "hash-a" for item in responses)

    body, headers = _signed()
    response = client.get(
        "/v0/issues/similar-issues/grouping-record/delete/2", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["deleted"] >= 1


def test_anomaly_detection_endpoints(client):
    body, headers = _signed(
        {
            "organization_id": ORG_ID,
            "project_id": 2,
            "config": {"time_period": 60, "sensitivity": "medium", "direction": "both", "expected_seasonality": "auto"},
            "context": [
                {"timestamp": 1, "value": 10},
                {"timestamp": 2, "value": 11},
                {"timestamp": 3, "value": 10},
                {"timestamp": 4, "value": 1000},
            ],
        }
    )
    response = client.post("/v1/anomaly-detection/detect", content=body, headers=headers)
    assert response.status_code == 200
    series = response.json()["timeseries"]
    assert any(point["anomaly"]["anomaly_type"] == "anomaly_higher_confidence" for point in series)

    body, headers = _signed({"organization_id": ORG_ID, "project_id": 2})
    response = client.post("/v1/anomaly-detection/store", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_code_review_contract_is_accepted(client):
    body, headers = _signed({"data": {"repo": {}, "pr_id": 1}, "external_owner_id": "1"})
    response = client.post("/v1/code_review/review-request", content=body, headers=headers)
    assert response.status_code == 202
    body, headers = _signed({"data": {"repo": {}, "pr_id": 1}, "external_owner_id": "1"})
    response = client.post("/v1/code_review/pr-closed", content=body, headers=headers)
    assert response.status_code == 200


def test_agent_loop_calls_tools_then_answers(monkeypatch):
    from types import SimpleNamespace

    from tweaker.seer import agent_loop
    from tweaker.seer.rpc import SentryRPCClient
    from tweaker.services.llm.base import ToolCall, ToolTurn, Usage

    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def __init__(self):
            self.calls = 0

        def complete_with_tools(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ToolTurn(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            name="search_issues",
                            arguments={"query": "is:unresolved"},
                        )
                    ],
                    usage=Usage(input_tokens=10, output_tokens=5),
                )
            return ToolTurn(
                content="BACKEND-1A2B is the highest-volume issue.",
                usage=Usage(input_tokens=10, output_tokens=5),
            )

    def fake_call(self, method, args):
        if method == "execute_issues_query":
            return [
                {
                    "id": "1001",
                    "shortId": "BACKEND-1A2B",
                    "title": "KeyError",
                    "count": 10,
                }
            ]
        if method == "get_project_preferences":
            return {"repositories": []}
        return None

    monkeypatch.setattr(agent_loop, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(SentryRPCClient, "call", fake_call)

    run = SimpleNamespace(
        id=1,
        org_id=ORG_ID,
        project_id=2,
        viewer_context=None,
    )
    result = agent_loop.run_agent(run, "What is the top issue?", {})
    assert "BACKEND-1A2B" in result.answer
    assert result.tool_calls[0]["name"] == "search_issues"
    assert any(item["message"]["role"] == "tool_use" for item in result.blocks)
    assert result.usage.input_tokens == 20


def test_similar_issues_training_mode_returns_no_grouping(client):
    payload = {
        "project_id": 77,
        "hash": "training-hash",
        "exception_type": "KeyError",
        "stacktrace": "app.checkout DISCOUNTS code KeyError",
        "training_mode": True,
    }
    body, headers = _signed(payload)
    response = client.post("/v0/issues/similar-issues", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["responses"] == []


def test_single_run_emits_lifecycle_signals(client, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def recording_call(self, method, args):
        calls.append((method, args))
        return _fake_call(self, method, args)

    monkeypatch.setattr(SentryRPCClient, "call", recording_call)
    payload = {
        "feature_id": "autofix_single_run",
        "payload": {
            "group_id": GROUP_ID,
            "project_id": 2,
            "short_id": "BACKEND-1A2B",
            "title": "KeyError: 'SPRING25'",
            "culprit": "app.checkout.apply_discount",
            "stopping_point": "code_changes",
        },
        "referrer": "api.web",
        "ref": "33333333-3333-3333-3333-333333333333",
    }
    body, headers = _signed(payload)
    response = client.post("/v1/automation/agent/feature/run", content=body, headers=headers)
    assert response.status_code == 200
    session = _wait(client, response.json()["run_id"])
    assert session["status"] == "completed"

    methods = [method for method, _ in calls]
    assert "send_seer_webhook" in methods
    assert "deliver_feature_result" in methods
    names = [
        args["event_name"]
        for method, args in calls
        if method == "send_seer_webhook"
    ]
    assert "root_cause_started" in names
    assert "root_cause_completed" in names
    assert "solution_started" in names
    assert "solution_completed" in names
    assert "coding_started" in names
    assert "coding_completed" in names

    delivery = next(args for method, args in calls if method == "deliver_feature_result")
    assert delivery["run_uuid"] == "33333333-3333-3333-3333-333333333333"
    assert delivery["status"] == "completed"


def test_index_endpoint_accepts_and_exports(client):
    body, headers = _signed({"organization_id": ORG_ID, "project_ids": [2]})
    response = client.post(
        "/v1/automation/explorer/index", content=body, headers=headers
    )
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"

    body, headers = _signed({"org_id": ORG_ID})
    response = client.post(
        "/v1/automation/explorer/export-indexes", content=body, headers=headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["org_id"] == ORG_ID
    assert data["version"] == 1
    assert isinstance(data["tables"], dict)


def test_select_solution_generates_code_changes(client, monkeypatch):
    monkeypatch.setattr(SentryRPCClient, "call", _fake_call)
    from tweaker.db import session_scope
    from tweaker.models import SeerRun
    from tweaker.seer.state import block

    with session_scope() as session:
        run = SeerRun(
            org_id=ORG_ID,
            org_slug="acme",
            project_id=2,
            group_id=GROUP_ID,
            feature_id="autofix",
            kind="feature",
            status="completed",
            title="Autofix — BACKEND-1A2B",
            blocks=[block(step="root_cause", content="Unknown coupon code.")],
            payload={"step": "solution"},
            run_metadata={
                "repos": [
                    {
                        "provider": "github",
                        "owner": "acme",
                        "name": "store",
                        "external_id": "42",
                        "branch_name": "main",
                        "code_mappings": [],
                    }
                ]
            },
            repo_pr_states={},
            coding_agents={},
            queued_feedback=[],
            usage={"usages": []},
            referrer="api.web",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    body, headers = _signed(
        {
            "run_id": run_id,
            "organization_id": ORG_ID,
            "payload": {
                "type": "select_solution",
                "solution": {"one_line_summary": "Guard the discount lookup."},
            },
        }
    )
    response = client.post(
        "/v1/automation/explorer/update", content=body, headers=headers
    )
    assert response.status_code == 202
    session = _wait(client, run_id)
    assert session["status"] == "completed"
    steps = [block["message"]["metadata"]["step"] for block in session["blocks"]]
    assert "code_changes" in steps
    code = next(
        block
        for block in session["blocks"]
        if block["message"]["metadata"]["step"] == "code_changes"
    )
    assert code["merged_file_patches"]


def test_delegated_agent_match_by_pull_request_id(client):
    from tweaker.db import session_scope
    from tweaker.models import SeerRun

    with session_scope() as session:
        run = SeerRun(
            org_id=ORG_ID,
            org_slug="acme",
            project_id=2,
            group_id=GROUP_ID,
            feature_id="autofix",
            kind="feature",
            status="completed",
            title="PR id match",
            blocks=[],
            payload={},
            run_metadata={},
            repo_pr_states={
                "acme/store": {
                    "repo_name": "acme/store",
                    "provider": "github",
                    "branch_name": "tweaker/fix-77",
                    "pr_url": "https://github.com/acme/store/pull/77",
                    "pr_id": "77",
                    "pr_number": 77,
                    "pr_creation_status": "completed",
                }
            },
            coding_agents={},
            queued_feedback=[],
            usage={"usages": []},
            referrer="api.web",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    body, headers = _signed(
        {
            "organization_id": ORG_ID,
            "pull_request_id": 77,
            "repo": {"owner": "acme", "name": "store"},
        }
    )
    response = client.post(
        "/v1/pr-metrics/delegated-agent-match", content=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
    assert response.json()["signal_type"] == "pull_request_id"


def test_llm_generate_honors_schema_and_model(client, monkeypatch):
    from tweaker.seer import endpoints
    from tweaker.services.llm.base import Usage

    class FakeProvider:
        name = "fake"
        model = "default-model"

        def complete_text(self, *, system, prompt, max_tokens=4000, model=None):
            assert model == "requested-model"
            assert "properties" in prompt
            return '{"title": "Checkout failures"}', Usage(input_tokens=3, output_tokens=2)

    monkeypatch.setattr(endpoints, "get_provider", lambda: FakeProvider())
    body, headers = _signed(
        {
            "prompt": "Name this search",
            "model": "requested-model",
            "response_schema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
            },
        }
    )
    response = client.post("/v1/llm/generate", content=body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["response"] == {"title": "Checkout failures"}
    assert data["usage"]["model"] == "requested-model"


def test_user_input_response_resumes_explorer_run(client):
    from tweaker.db import session_scope
    from tweaker.models import SeerRun

    with session_scope() as session:
        run = SeerRun(
            org_id=ORG_ID,
            org_slug="acme",
            project_id=2,
            group_id=GROUP_ID,
            feature_id=None,
            kind="explorer",
            status="awaiting_user_input",
            title="Seer Agent",
            blocks=[],
            payload={"query": "Investigate this issue"},
            run_metadata={
                "pending_user_input": {
                    "id": "pending-1",
                    "input_type": "ask_user_question",
                    "data": {"question": "Which repo?"},
                }
            },
            repo_pr_states={},
            coding_agents={},
            queued_feedback=[],
            usage={"usages": []},
            referrer="seer.explorer",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    body, headers = _signed(
        {
            "run_id": run_id,
            "organization_id": ORG_ID,
            "payload": {
                "type": "user_input_response",
                "response_data": {"response": "acme/store"},
            },
        }
    )
    response = client.post(
        "/v1/automation/explorer/update", content=body, headers=headers
    )
    assert response.status_code == 202
    session = _wait(client, run_id)
    assert session["status"] == "completed"
    assert session["pending_user_input"] is None
    assert any(
        block["message"]["role"] == "user"
        and "acme/store" in block["message"]["content"]
        for block in session["blocks"]
    )
