from __future__ import annotations

import hashlib
import hmac
import json
import time


def test_sentry_webhook_requires_signature(client):
    response = client.post("/api/webhooks/sentry", json={"action": "created"})
    assert response.status_code == 401


def test_sentry_webhook_accepts_valid_signature(client):
    org = client.post(
        "/api/orgs",
        json={
            "slug": f"webhook-{int(time.time())}",
            "name": "Webhook Test",
            "sentry_url": "https://sentry.example.com",
            "sentry_org_slug": "webhook",
            "webhook_secret": "client-secret",
        },
    ).json()
    payload = {
        "action": "created",
        "installation": {"uuid": "installation-1"},
        "data": {
            "issue": {
                "id": "9001",
                "shortId": "WEB-9001",
                "title": "ValueError: bad input",
                "culprit": "app.main",
                "level": "error",
                "status": "unresolved",
                "platform": "python",
                "firstSeen": "2026-01-01T00:00:00Z",
                "lastSeen": "2026-01-01T00:01:00Z",
                "count": 3,
                "userCount": 1,
                "permalink": "https://sentry.example.com/issues/9001/",
                "metadata": {"type": "ValueError", "value": "bad input"},
                "project": {"slug": "backend"},
            }
        },
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(b"client-secret", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/api/webhooks/sentry",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Sentry-Hook-Resource": "issue",
            "Sentry-Hook-Signature": signature,
        },
    )
    assert response.status_code == 202, response.text
    issues = client.get(f"/api/issues?org_id={org['id']}&status=all").json()
    assert any(item["short_id"] == "WEB-9001" for item in issues["items"])
