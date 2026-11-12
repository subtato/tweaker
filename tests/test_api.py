from __future__ import annotations

import time


def wait_for_run(client, run_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        data = response.json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.1)
    raise AssertionError(f"Run {run_id} did not finish")


def test_health_and_config(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json()["demo_mode"] is True


def test_demo_issues_are_seeded(client):
    response = client.get("/api/issues")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 5
    short_ids = {item["short_id"] for item in data["items"]}
    assert "BACKEND-1A2B" in short_ids


def test_issue_detail_contains_event(client, issue):
    response = client.get(f"/api/issues/{issue['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["latest_event"]["exceptions"]
    assert data["latest_event"]["exceptions"][0]["stacktrace"]["frames"]


def test_investigation_produces_patch(client, issue):
    response = client.post(f"/api/issues/{issue['id']}/investigate", json={"mode": "autofix"})
    assert response.status_code == 202
    run_id = response.json()["id"]
    run = wait_for_run(client, run_id)
    assert run["status"] == "succeeded", run
    assert run["root_cause"]
    assert run["patch"]
    assert run["fixability_score"] >= 70
    step_titles = [step["title"] for step in run["steps"]]
    assert "Analyzing root cause" in step_titles
    assert "Validating proposed fix" in step_titles

    patch = client.get(f"/api/runs/{run_id}/patch")
    assert patch.status_code == 200
    assert patch.text.startswith("--- a/")


def test_ask_returns_citations(client, org):
    response = client.post(
        "/api/ask",
        json={"org_id": org["id"], "question": "Which issue should on-call look at first?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"]
    assert data["citations"]
    assert data["citations"][0]["title"].startswith("BACKEND-1A2B")


def test_stats(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["open_issues"] >= 5
    assert data["investigated"] >= 1


def test_scan(client, issue):
    response = client.post(f"/api/issues/{issue['id']}/scan", json={"force": False})
    assert response.status_code == 200
    data = response.json()
    assert 0 <= data["fixability_score"] <= 100


def test_usage_and_quota(client):
    from tweaker.services.usage import check_quota, record_usage, set_quota

    org_id = "quota-test"
    record_usage(
        org_id=org_id,
        run_id=None,
        kind="chat",
        provider="demo",
        model="demo",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.01,
    )
    response = client.get(f"/api/usage?org_id={org_id}")
    assert response.status_code == 200
    assert response.json()["total_tokens"] == 15
    set_quota(org_id, monthly_token_limit=10, monthly_cost_limit=None, monthly_run_limit=None)
    allowed, reason = check_quota(org_id)
    assert allowed is False
    assert "token" in (reason or "").lower()
    response = client.put(
        f"/api/usage/quota/{org_id}",
        json={"monthly_token_limit": 100000, "monthly_run_limit": 10},
    )
    assert response.status_code == 200
    assert response.json()["monthly_token_limit"] == 100000


def test_metrics_endpoint(client):
    response = client.get("/api/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_repo_index_endpoint(client):
    org = client.get("/api/orgs").json()[0]
    repo = client.get(f"/api/orgs/{org['id']}/repos").json()[0]
    response = client.post(f"/api/repos/{repo['id']}/index")
    assert response.status_code == 200
    assert response.json()["indexed"] >= 3
    stats = client.get(f"/api/repos/{repo['id']}/index")
    assert stats.status_code == 200
    assert stats.json()["files"] >= 3


def test_code_reviews_api(client):
    response = client.get("/api/code-reviews")
    assert response.status_code == 200
    assert "items" in response.json()
