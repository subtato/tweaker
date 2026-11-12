from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import respx

from tweaker.seer import codereview
from tweaker.seer.codereview import create_code_review, execute_code_review


def _diff() -> str:
    return (
        "--- a/app/checkout.py\n"
        "+++ b/app/checkout.py\n"
        "@@ -1,3 +1,4 @@\n"
        " DISCOUNTS = {\n"
        '+    "SPRING25": 0.25,\n'
        " }\n"
    )


@respx.mock
def test_code_review_check_run_and_inline_comments(client, monkeypatch):
    settings = SimpleNamespace(
        github_token="gh-token",
        gitlab_token=None,
        github_base_url="https://github.com",
        gitlab_base_url="https://gitlab.com",
    )
    monkeypatch.setattr(codereview, "get_settings", lambda: settings)
    monkeypatch.setattr(
        codereview,
        "_fetch_pr_metadata",
        lambda *args: {
            "head_sha": "head-sha",
            "base_sha": "base-sha",
            "title": "Add spring campaign",
            "description": "Adds a coupon",
            "url": "https://github.com/acme/store/pull/7",
            "state": "open",
        },
    )
    monkeypatch.setattr(codereview, "_fetch_diff", lambda *args: _diff())
    monkeypatch.setattr(
        codereview,
        "_review_diff",
        lambda *args, **kwargs: {
            "summary": "One coupon issue.",
            "comments": [
                {
                    "path": "app/checkout.py",
                    "line": 2,
                    "severity": "medium",
                    "body": "This campaign is not present in the discount table.",
                }
            ],
        },
    )

    check_route = respx.post("https://api.github.com/repos/acme/store/check-runs").mock(
        return_value=httpx.Response(
            201, json={"id": 55, "html_url": "https://github.com/acme/store/runs/55"}
        )
    )
    review_route = respx.post("https://api.github.com/repos/acme/store/pulls/7/reviews").mock(
        return_value=httpx.Response(
            200, json={"html_url": "https://github.com/acme/store/pull/7#review"}
        )
    )
    complete_route = respx.patch(
        "https://api.github.com/repos/acme/store/check-runs/55"
    ).mock(return_value=httpx.Response(200, json={"id": 55}))

    item = create_code_review(
        {
            "data": {
                "repo": {
                    "provider": "github",
                    "owner": "acme",
                    "name": "store",
                    "external_id": "42",
                    "base_commit_sha": "base-sha",
                },
                "pr_id": 7,
            },
            "external_owner_id": "1",
        },
        org_id=4242,
    )
    execute_code_review(item.id)

    from tweaker.db import get_session_factory
    from tweaker.models import SeerCodeReview

    session = get_session_factory()()
    stored = session.get(SeerCodeReview, item.id)
    assert stored.status == "completed"
    assert stored.check_run_id == "55"
    assert stored.review_url == "https://github.com/acme/store/pull/7#review"
    assert stored.comment_count == 1
    assert stored.conclusion == "neutral"
    assert check_route.called
    assert review_route.called
    assert complete_route.called

    check_payload = json.loads(check_route.calls[0].request.content)
    assert check_payload["head_sha"] == "head-sha"
    assert check_payload["status"] == "in_progress"

    review_payload = json.loads(review_route.calls[0].request.content)
    assert review_payload["commit_id"] == "head-sha"
    assert review_payload["comments"][0]["path"] == "app/checkout.py"
    assert review_payload["comments"][0]["line"] == 2

    complete_payload = json.loads(complete_route.calls[0].request.content)
    assert complete_payload["status"] == "completed"
    assert complete_payload["conclusion"] == "neutral"
    assert complete_payload["output"]["annotations"][0]["path"] == "app/checkout.py"
    session.close()


@respx.mock
def test_code_review_rerun_creates_new_check_run(client, monkeypatch):
    settings = SimpleNamespace(
        github_token="gh-token",
        gitlab_token=None,
        github_base_url="https://github.com",
        gitlab_base_url="https://gitlab.com",
    )
    monkeypatch.setattr(codereview, "get_settings", lambda: settings)
    monkeypatch.setattr(
        codereview,
        "_fetch_pr_metadata",
        lambda *args: {"head_sha": "head-sha", "base_sha": "base-sha", "title": "", "description": ""},
    )
    monkeypatch.setattr(codereview, "_fetch_diff", lambda *args: _diff())
    monkeypatch.setattr(
        codereview,
        "_review_diff",
        lambda *args, **kwargs: {"summary": "Clean.", "comments": []},
    )
    respx.post("https://api.github.com/repos/acme/store/check-runs").mock(
        return_value=httpx.Response(201, json={"id": 56})
    )
    review_route = respx.post("https://api.github.com/repos/acme/store/pulls/7/reviews").mock(
        return_value=httpx.Response(200, json={"html_url": "https://github.com/acme/store/pull/7#review"})
    )
    respx.patch("https://api.github.com/repos/acme/store/check-runs/56").mock(
        return_value=httpx.Response(200, json={"id": 56})
    )
    item = create_code_review(
        {
            "data": {
                "repo": {
                    "provider": "github",
                    "owner": "acme",
                    "name": "store",
                    "base_commit_sha": "base-sha",
                },
                "pr_id": 7,
            }
        },
        org_id=4242,
    )
    execute_code_review(item.id)
    from tweaker.db import get_session_factory
    from tweaker.models import SeerCodeReview

    session = get_session_factory()()
    stored = session.get(SeerCodeReview, item.id)
    assert stored.conclusion == "success"
    assert stored.check_run_id == "56"
    assert review_route.called
    session.close()


def test_code_review_rerun_reuses_original_review(client):
    original = create_code_review(
        {
            "data": {
                "repo": {
                    "provider": "github",
                    "owner": "acme",
                    "name": "store",
                    "external_id": "42",
                    "base_commit_sha": "base-sha",
                },
                "pr_id": 7,
            }
        },
        org_id=4242,
    )
    rerun = create_code_review({"original_run_id": str(original.id)}, org_id=4242)
    assert rerun.id != original.id
    assert rerun.provider == "github"
    assert rerun.owner == "acme"
    assert rerun.name == "store"
    assert rerun.pr_id == "7"
    assert rerun.status == "processing"

    # The external_id on the check run is the review id, so a rerun can also
    # arrive addressed by check_run_id.
    from tweaker.db import get_session_factory
    from tweaker.models import SeerCodeReview

    session = get_session_factory()()
    stored = session.get(SeerCodeReview, original.id)
    stored.check_run_id = "987"
    session.commit()
    session.close()
    by_check_run = create_code_review({"original_run_id": "987"}, org_id=4242)
    assert by_check_run.pr_id == "7"


@respx.mock
def test_code_review_gitlab_status_and_discussions(client, monkeypatch):
    settings = SimpleNamespace(
        github_token=None,
        gitlab_token="gl-token",
        github_base_url="https://github.com",
        gitlab_base_url="https://gitlab.com",
    )
    monkeypatch.setattr(codereview, "get_settings", lambda: settings)
    monkeypatch.setattr(
        codereview,
        "_fetch_pr_metadata",
        lambda *args: {
            "head_sha": "head-sha",
            "base_sha": "base-sha",
            "title": "Add spring campaign",
            "description": "",
            "url": "https://gitlab.com/acme/store/-/merge_requests/7",
        },
    )
    monkeypatch.setattr(codereview, "_fetch_diff", lambda *args: _diff())
    monkeypatch.setattr(
        codereview,
        "_review_diff",
        lambda *args, **kwargs: {
            "summary": "One coupon issue.",
            "comments": [
                {
                    "path": "app/checkout.py",
                    "line": 2,
                    "severity": "high",
                    "body": "This campaign is missing.",
                }
            ],
        },
    )
    status_route = respx.post(
        "https://gitlab.com/api/v4/projects/acme%2Fstore/statuses/head-sha"
    ).mock(return_value=httpx.Response(201, json={"id": 1}))
    discussion_route = respx.post(
        "https://gitlab.com/api/v4/projects/acme%2Fstore/merge_requests/7/discussions"
    ).mock(return_value=httpx.Response(201, json={"id": "d1"}))
    note_route = respx.post(
        "https://gitlab.com/api/v4/projects/acme%2Fstore/merge_requests/7/notes"
    ).mock(return_value=httpx.Response(201, json={"id": 1}))

    item = create_code_review(
        {
            "data": {
                "repo": {
                    "provider": "gitlab",
                    "owner": "acme",
                    "name": "store",
                    "base_commit_sha": "base-sha",
                },
                "pr_id": 7,
            }
        },
        org_id=4242,
    )
    execute_code_review(item.id)

    from tweaker.db import get_session_factory
    from tweaker.models import SeerCodeReview

    session = get_session_factory()()
    stored = session.get(SeerCodeReview, item.id)
    assert stored.status == "completed"
    assert stored.conclusion == "failure"
    assert stored.comment_count == 1
    assert status_route.called
    assert discussion_route.called
    assert note_route.called
    session.close()
