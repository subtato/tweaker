from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="tweaker-tests-"))
os.environ.update(
    {
        "TWEAKER_DATA_DIR": str(_TEST_ROOT),
        "TWEAKER_DATABASE_URL": f"sqlite:///{_TEST_ROOT / 'test.db'}",
        "TWEAKER_DEMO_MODE": "true",
        "TWEAKER_SECRET_KEY": "test-secret-key-not-for-production",
        "TWEAKER_RUNNER": "inline",
        "TWEAKER_AI_PROVIDER": "demo",
        "TWEAKER_DEMO_REPO_PATH": str(Path(__file__).resolve().parents[1] / "examples" / "demo-repo"),
        "TWEAKER_SEER_API_SHARED_SECRET": "test-seer-api-secret-0123456789abcdef",
        "TWEAKER_SEER_RPC_SHARED_SECRET": "test-seer-rpc-secret-0123456789abcdef",
        "TWEAKER_REPO_MAP": json.dumps(
            {
                "acme/store": str(
                    Path(__file__).resolve().parents[1] / "examples" / "demo-repo"
                )
            }
        ),
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tweaker.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def org(client):
    orgs = client.get("/api/orgs").json()
    assert orgs
    return orgs[0]


@pytest.fixture(scope="session")
def issue(client):
    items = client.get("/api/issues").json()["items"]
    assert items
    return items[0]
