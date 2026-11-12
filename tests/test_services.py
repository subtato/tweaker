from __future__ import annotations

from types import SimpleNamespace

from tweaker.services.classifier import heuristic_fixability
from tweaker.services.patch import changed_files, check_applies, validate_patch
from tweaker.services.redaction import compact_event, redact_text


def _event(*, in_app: bool = True) -> dict:
    return {
        "eventID": "abc123",
        "dateCreated": "2026-01-01T00:00:00Z",
        "title": "KeyError: 'SPRING25'",
        "message": "SPRING25",
        "culprit": "app.checkout.apply_discount",
        "platform": "python",
        "tags": [{"key": "environment", "value": "production"}],
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
                                        "filename": "app/checkout.py",
                                        "function": "apply_discount",
                                        "lineNo": 7,
                                        "inApp": in_app,
                                        "contextLine": "discount = DISCOUNTS[code]",
                                    }
                                ]
                            },
                        }
                    ]
                },
            }
        ],
        "breadcrumbs": {"values": []},
    }


def test_redacts_tokens_and_secret_keys():
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    assert "abcdefghijklmnopqrstuvwxyz" not in redact_text(text)
    event = _event()
    event["extra"] = {"password": "hunter2", "safe": "visible"}
    compact = compact_event(event)
    assert compact["extra"]["password"] == "[REDACTED]"
    assert compact["extra"]["safe"] == "visible"


def test_compact_event_is_idempotent():
    compact = compact_event(_event())
    again = compact_event(compact)
    assert again["exceptions"][0]["type"] == "KeyError"
    assert again["exceptions"][0]["stacktrace"]["frames"][0]["filename"] == "app/checkout.py"


def test_classifier_prefers_in_app_source():
    issue = SimpleNamespace(repo_id="repo", issue_metadata={"type": "KeyError"})
    high = heuristic_fixability(issue, _event(), "app/checkout.py\n" + "x" * 500)
    low = heuristic_fixability(issue, _event(in_app=False), "")
    assert high.score > low.score
    assert high.score >= 70
    assert "in-application" in " ".join(high.signals)


def test_patch_validation_rejects_traversal():
    patch = "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n"
    result = validate_patch(patch)
    assert not result.ok
    assert "traversal" in result.message


def test_patch_validation_accepts_unified_diff():
    patch = (
        "--- a/app/checkout.py\n"
        "+++ b/app/checkout.py\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )
    result = validate_patch(patch)
    assert result.ok
    assert changed_files(patch) == ["app/checkout.py"]


def test_demo_patch_applies(client):
    from tweaker.services.llm.demo import _DEMO_PATCH

    repo = client.get(f"/api/orgs/{client.get('/api/orgs').json()[0]['id']}/repos").json()[0]
    from tweaker.db import get_session_factory
    from tweaker.models import Repository
    from tweaker.services.codebase import RepositoryWorkspace

    session = get_session_factory()()
    model = session.get(Repository, repo["id"])
    workspace = RepositoryWorkspace(model)
    result = check_applies(_DEMO_PATCH, workspace)
    assert result.ok, result.message
    session.close()


def test_code_index_search(client):
    from tweaker.db import get_session_factory
    from tweaker.models import Repository
    from tweaker.services.code_index import index_stats, index_workspace, search_index
    from tweaker.services.codebase import RepositoryWorkspace

    org = client.get("/api/orgs").json()[0]
    repo_data = client.get(f"/api/orgs/{org['id']}/repos").json()[0]
    session = get_session_factory()()
    repo = session.get(Repository, repo_data["id"])
    workspace = RepositoryWorkspace(repo)
    workspace.ensure()
    result = index_workspace(workspace, "acme/store", full=True)
    assert result["indexed"] >= 3
    assert index_stats(session, "acme/store")["files"] >= 3
    matches = search_index(session, repo_ids=["acme/store"], query="DISCOUNTS", limit=5)
    assert any("checkout.py" in item["path"] for item in matches)
    session.close()


def test_rate_limiter_window():
    from tweaker.services.rate_limit import SlidingWindowLimiter

    limiter = SlidingWindowLimiter()
    assert limiter.allow("key", limit=2, window_seconds=60)[0] is True
    assert limiter.allow("key", limit=2, window_seconds=60)[0] is True
    allowed, retry = limiter.allow("key", limit=2, window_seconds=60)
    assert allowed is False
    assert retry >= 1
