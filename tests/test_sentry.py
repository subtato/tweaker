from __future__ import annotations

import httpx
import respx

from tweaker.services.sentry import SentryClient, SentryError


@respx.mock
def test_list_issues_and_latest_event():
    base = "https://sentry.example.com"
    respx.get(f"{base}/api/0/projects/acme/backend/issues/").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "1001",
                    "shortId": "BACKEND-1A2B",
                    "title": "KeyError",
                    "project": {"slug": "backend"},
                }
            ],
        )
    )
    respx.get(f"{base}/api/0/issues/1001/events/latest/").mock(
        return_value=httpx.Response(200, json={"eventID": "abc", "entries": []})
    )
    with SentryClient(base, "token") as client:
        issues = client.list_issues("acme", project_slug="backend")
        assert issues[0]["shortId"] == "BACKEND-1A2B"
        event = client.get_latest_event("acme", "1001")
        assert event["eventID"] == "abc"


@respx.mock
def test_latest_event_falls_back_to_list():
    base = "https://sentry.example.com"
    respx.get(f"{base}/api/0/issues/1001/events/latest/").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    respx.get(f"{base}/api/0/organizations/acme/issues/1001/events/").mock(
        return_value=httpx.Response(200, json=[{"eventID": "fallback"}])
    )
    with SentryClient(base, "token") as client:
        event = client.get_latest_event("acme", "1001")
    assert event["eventID"] == "fallback"


@respx.mock
def test_sentry_error_has_status():
    base = "https://sentry.example.com"
    respx.get(f"{base}/api/0/issues/404/").mock(
        return_value=httpx.Response(404, json={"detail": "Issue not found"})
    )
    with SentryClient(base, "token") as client:
        try:
            client.get_issue("404")
        except SentryError as exc:
            assert exc.status_code == 404
            assert "Issue not found" in str(exc)
        else:
            raise AssertionError("Expected SentryError")


def test_rejects_non_http_url():
    try:
        SentryClient("file:///etc/passwd", "token")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")
