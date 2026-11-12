from __future__ import annotations

from types import SimpleNamespace

from tweaker.seer import anomaly, assisted, indexing, lifecycle
from tweaker.seer.agent_loop import tool_specs
from tweaker.seer.repos import RepoDefinition
from tweaker.seer.rpc import SentryRPCClient
from tweaker.services.llm.base import ToolCall, ToolTurn, Usage

ORG_ID = 4242


def _run(**overrides):
    values = {
        "id": 7,
        "org_id": ORG_ID,
        "group_id": 1001,
        "project_id": 2,
        "feature_id": "autofix",
        "kind": "feature",
        "status": "completed",
        "viewer_context": None,
        "sentry_run_id": "11111111-1111-1111-1111-111111111111",
        "blocks": [],
        "repo_pr_states": {},
        "run_metadata": {},
        "referrer": "api.web",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_lifecycle_emits_step_webhooks_and_pr_notification(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_call(self, method, args):
        calls.append((method, args))
        return None

    monkeypatch.setattr(SentryRPCClient, "call", fake_call)
    run = _run()
    lifecycle.emit_step_started(run, "root_cause")
    lifecycle.emit_step_completed(run, "root_cause", {"root_cause": {"one_line_description": "x"}})
    repo = RepoDefinition(provider="github", owner="acme", name="store", external_id="42")
    state = {
        "pr_number": 9,
        "pr_url": "https://github.com/acme/store/pull/9",
        "provider": "github",
    }
    entry = lifecycle.pull_request_entry(repo, state)
    lifecycle.notify_pr_created(run, [entry])
    lifecycle.emit_pr_created(run, [entry])

    methods = [method for method, _ in calls]
    assert "notify_seer_pr_created" in methods
    webhooks = [args for method, args in calls if method == "send_seer_webhook"]
    names = [item["event_name"] for item in webhooks]
    assert "root_cause_started" in names
    assert "root_cause_completed" in names
    assert "pr_created" in names
    assert "pr_ready_for_review" in names

    notify = next(args for method, args in calls if method == "notify_seer_pr_created")
    assert notify["run_id"] == 7
    assert notify["group_id"] == 1001
    assert notify["pull_requests"][0]["repo_external_id"] == "42"
    assert notify["pull_requests"][0]["pull_request"]["pr_number"] == 9


def test_lifecycle_swallows_transport_errors(monkeypatch):
    def fail_call(self, method, args):
        raise RuntimeError("sentry unavailable")

    monkeypatch.setattr(SentryRPCClient, "call", fail_call)
    # None of these should raise.
    lifecycle.emit_step_started(_run(), "root_cause")
    lifecycle.notify_pr_created(_run(), [{"repo_name": "acme/store"}])
    lifecycle.deliver_feature_result(_run(feature_id="autofix_single_run"), status="completed")


def test_lifecycle_can_be_disabled(monkeypatch):
    calls: list[str] = []

    def fake_call(self, method, args):
        calls.append(method)
        return None

    monkeypatch.setattr(SentryRPCClient, "call", fake_call)
    monkeypatch.setattr(lifecycle, "_enabled", lambda: False)
    run = _run()
    lifecycle.emit_step_started(run, "root_cause")
    lifecycle.notify_pr_created(run, [{"repo_name": "acme/store"}])
    lifecycle.deliver_feature_result(
        _run(feature_id="autofix_single_run"), status="completed"
    )
    assert calls == []


def test_agent_tool_surface_and_investigation_gate():
    names = {item.name for item in tool_specs()}
    assert {
        "get_project_members",
        "get_team_members",
        "get_attribute_names",
        "get_repository",
        "execute_table_query",
        "execute_timeseries_query",
        "get_issues_by_query",
        "list_directory",
        "find_files",
        "get_file_history",
        "git_blame",
        "get_commit",
        "ask_user_question",
    } <= names

    code_only = {item.name for item in tool_specs({"enable_investigation_tools": False})}
    assert "search_issues" not in code_only
    assert "execute_table_query" not in code_only
    assert "list_directory" in code_only
    assert "ask_user_question" in code_only


def test_agent_calls_new_rpc_tool_then_answers(monkeypatch):
    from tweaker.seer import agent_loop

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
                            name="get_project_members",
                            arguments={"project_id": 2, "limit": 3},
                        )
                    ],
                    usage=Usage(input_tokens=4, output_tokens=2),
                )
            return ToolTurn(
                content="The most likely owner is Sam.",
                usage=Usage(input_tokens=4, output_tokens=2),
            )

    def fake_call(self, method, args):
        if method == "get_project_preferences":
            return {"repositories": []}
        if method == "get_project_members":
            return {"members": [{"type": "user", "username": "sam"}]}
        return None

    monkeypatch.setattr(agent_loop, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(SentryRPCClient, "call", fake_call)

    result = agent_loop.run_agent(_run(), "Who owns this?", {})
    assert result.status == "completed"
    assert "Sam" in result.answer
    assert result.tool_calls[0]["name"] == "get_project_members"
    tool_block = next(
        item for item in result.blocks if item["message"]["role"] == "tool_use"
    )
    assert tool_block["tool_results"][0]["content"].find("sam") >= 0


def test_agent_code_and_query_tools(monkeypatch):
    from tweaker.seer import agent_loop

    rpc_calls: list[tuple[str, dict]] = []

    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def __init__(self):
            self.turn = 0

        def complete_with_tools(self, **kwargs):
            self.turn += 1
            if self.turn == 1:
                return ToolTurn(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="list-1",
                            name="list_directory",
                            arguments={"path": "app"},
                        )
                    ],
                    usage=Usage(),
                )
            if self.turn == 2:
                return ToolTurn(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="query-1",
                            name="execute_table_query",
                            arguments={
                                "dataset": "events",
                                "fields": ["count()"],
                                "per_page": 5,
                                "stats_period": "24h",
                            },
                        )
                    ],
                    usage=Usage(),
                )
            return ToolTurn(content="Done", usage=Usage())

    def fake_call(self, method, args):
        rpc_calls.append((method, args))
        if method == "get_project_preferences":
            return {
                "repositories": [
                    {
                        "provider": "github",
                        "owner": "acme",
                        "name": "store",
                        "branch_name": "main",
                    }
                ]
            }
        if method == "execute_table_query":
            return {"data": [{"count()": 3}]}
        return None

    monkeypatch.setattr(agent_loop, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(SentryRPCClient, "call", fake_call)

    result = agent_loop.run_agent(_run(), "Inspect the app directory", {})
    assert result.answer == "Done"
    tool_blocks = [
        item for item in result.blocks if item["message"]["role"] == "tool_use"
    ]
    assert "checkout.py" in tool_blocks[0]["tool_results"][0]["content"]
    assert "count()" in tool_blocks[1]["tool_results"][0]["content"]
    query = next(args for method, args in rpc_calls if method == "execute_table_query")
    assert query["org_id"] == ORG_ID
    assert query["project_ids"] == [2]


def test_agent_calls_custom_tool(monkeypatch):
    from tweaker.seer import agent_loop

    calls: list[tuple[str, dict]] = []

    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def __init__(self):
            self.turn = 0

        def complete_with_tools(self, **kwargs):
            self.turn += 1
            if self.turn == 1:
                return ToolTurn(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="custom-1",
                            name="lookup_owner",
                            arguments={"team": "checkout"},
                        )
                    ],
                    usage=Usage(),
                )
            return ToolTurn(content="The owner is Sam.", usage=Usage())

    def fake_call(self, method, args):
        calls.append((method, args))
        if method == "get_project_preferences":
            return {"repositories": []}
        if method == "call_custom_tool":
            return {"owners": ["sam"]}
        return None

    monkeypatch.setattr(agent_loop, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(SentryRPCClient, "call", fake_call)

    payload = {
        "custom_tools": [
            {
                "name": "lookup_owner",
                "module_path": "sentry.custom.OwnerLookup",
                "description": "Look up a team owner",
                "param_schema": {
                    "type": "object",
                    "properties": {"team": {"type": "string"}},
                },
            }
        ]
    }
    result = agent_loop.run_agent(_run(), "Who owns checkout?", payload)
    assert result.answer == "The owner is Sam."
    assert result.tool_calls[0]["name"] == "lookup_owner"
    tool_block = next(
        item for item in result.blocks if item["message"]["role"] == "tool_use"
    )
    assert "sam" in tool_block["tool_results"][0]["content"]
    custom_call = next(args for method, args in calls if method == "call_custom_tool")
    assert custom_call["module_path"] == "sentry.custom.OwnerLookup"
    assert custom_call["team"] == "checkout"


def test_agent_pauses_for_user_input(monkeypatch):
    from tweaker.seer import agent_loop

    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def complete_with_tools(self, **kwargs):
            return ToolTurn(
                content="",
                tool_calls=[
                    ToolCall(
                        id="ask-1",
                        name="ask_user_question",
                        arguments={
                            "question": "Which repository should I inspect?",
                            "options": ["acme/store", "acme/web"],
                        },
                    )
                ],
                usage=Usage(input_tokens=3, output_tokens=1),
            )

    def fake_call(self, method, args):
        if method == "get_project_preferences":
            return {"repositories": []}
        return None

    monkeypatch.setattr(agent_loop, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(SentryRPCClient, "call", fake_call)

    result = agent_loop.run_agent(_run(), "Investigate", {})
    assert result.status == "awaiting_user_input"
    assert result.pending_user_input is not None
    assert result.pending_user_input["input_type"] == "ask_user_question"
    assert result.pending_user_input["data"]["options"] == ["acme/store", "acme/web"]


def test_anomaly_data_round_trip():
    points = [
        {"timestamp": 10, "value": 5, "yhat_lower": 1, "yhat_upper": 9},
        {"timestamp": 20, "value": 7, "yhat_lower": 2, "yhat_upper": 12},
    ]
    stored = anomaly.store(
        {
            "organization_id": ORG_ID,
            "project_id": 2,
            "alert": {"source_id": 55, "source_type": 1},
            "config": {"time_period": 60},
            "timeseries": points,
        }
    )
    assert stored["success"] is True

    data = anomaly.alert_data(
        {
            "alert": {"source_id": 55},
            "start": 15,
            "end": 30,
        }
    )
    assert data["success"] is True
    assert len(data["data"]) == 1
    assert data["data"][0]["external_alert_id"] == 55
    assert data["data"][0]["value"] == 7

    deleted = anomaly.delete({"organization_id": ORG_ID, "alert": {"source_id": 55}})
    assert deleted["success"] is True
    assert anomaly.alert_data({"alert": {"source_id": 55}})["data"] == []


def test_anomaly_detection_honors_direction_and_sensitivity():
    result = anomaly.detect(
        {
            "config": {"sensitivity": "high", "direction": "below"},
            "context": [
                {"timestamp": index, "value": value}
                for index, value in enumerate([10, 11, 10, 12, 10, 11, 10, 1000])
            ],
        }
    )
    kinds = [point["anomaly"]["anomaly_type"] for point in result["timeseries"]]
    assert "anomaly_higher_confidence" not in kinds
    assert all(kind in {"none", "anomaly_lower_confidence"} for kind in kinds)


def test_indexing_demo_repo_round_trip():
    result = indexing.index_org_repos(ORG_ID, None)
    assert result["indexed"] > 0
    exported = indexing.export_org_indexes(ORG_ID, None)
    assert exported["version"] == 1
    assert "github:acme/store" in exported["tables"]
    assert exported["file_count"] > 0
    files = exported["tables"]["github:acme/store"]["files"]
    assert any(item["path"].endswith("checkout.py") for item in files)


def test_compare_cohorts_reports_effect_size():
    result = assisted.compare_cohorts(
        {
            "baseline": [{"duration": 10}, {"duration": 11}, {"duration": 9}],
            "outliers": [{"duration": 100}, {"duration": 110}, {"duration": 90}],
        }
    )
    comparison = result["comparison"]["duration"]
    assert comparison["significant"] is True
    assert comparison["delta"] > 0
    assert comparison["relative_change"] > 1


def test_assisted_execution_uses_rpc(monkeypatch):
    class FakeRPC:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute_timeseries_query(self, **kwargs):
            return {"data": [[1, 2, 3]], "kwargs": kwargs}

    monkeypatch.setattr(assisted, "get_settings", lambda: SimpleNamespace(demo_mode=False))
    monkeypatch.setattr(assisted, "SentryRPCClient", FakeRPC)
    item = SimpleNamespace(
        org_id=ORG_ID,
        project_ids=[2],
        strategy="Traces",
        options={"stats_period": "7d"},
        viewer_context=None,
    )
    result = assisted._execute_translation(
        item, {"query": "span.duration:>1s", "dataset": "spans"}
    )
    assert result["executed"] is True
    assert result["dataset"] == "spans"
    assert result["results"]["kwargs"]["org_id"] == ORG_ID
