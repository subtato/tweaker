from __future__ import annotations

import logging
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from .config import get_settings
from .db import session_scope
from .models import (
    AskConversation,
    AuditEvent,
    Organization,
    Repository,
    Run,
    RunStep,
    SeerRun,
)
from .seer.diff import unified_diff_to_patches
from .services.ingest import upsert_issue
from .services.llm.demo import _DEMO_PATCH

logger = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _frame(
    filename: str,
    function: str,
    line: int,
    *,
    in_app: bool = True,
    context_line: str = "",
    pre: list[str] | None = None,
    post: list[str] | None = None,
    module: str | None = None,
) -> dict[str, Any]:
    return {
        "filename": filename,
        "absPath": f"/srv/acme/{filename}",
        "function": function,
        "module": module or function.rsplit(".", 1)[0],
        "lineNo": line,
        "colNo": 0,
        "inApp": in_app,
        "contextLine": context_line,
        "preContext": pre or [],
        "postContext": post or [],
    }


def _event(
    *,
    event_id: str,
    exc_type: str,
    exc_value: str,
    platform: str,
    frames: list[dict[str, Any]],
    tags: dict[str, str],
    created: datetime,
    culprit: str,
    breadcrumbs: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    contexts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "eventID": event_id,
        "id": event_id,
        "dateCreated": _iso(created),
        "title": f"{exc_type}: {exc_value}",
        "message": exc_value,
        "culprit": culprit,
        "platform": platform,
        "release": {"version": "store@1.8.2"},
        "tags": [{"key": key, "value": value} for key, value in tags.items()],
        "entries": [
            {
                "type": "exception",
                "data": {
                    "values": [
                        {
                            "type": exc_type,
                            "value": exc_value,
                            "mechanism": {"type": "generic", "handled": False},
                            "stacktrace": {"frames": frames},
                        }
                    ]
                },
            }
        ],
        "breadcrumbs": {"values": breadcrumbs or []},
        "contexts": contexts
        or {
            "runtime": {"name": "CPython", "version": "3.12.4"},
            "os": {"name": "Linux", "version": "6.8.0"},
            "trace": {"trace_id": "8f1c2b3d4e5f60718293a4b5c6d7e8f9"},
        },
        "user": {"id": "u_8421", "email": "sam@acme.test"},
        "request": request or {"url": "https://shop.acme.test/api/checkout", "method": "POST"},
        "extra": extra or {},
    }


def _issue_payload(
    *,
    issue_id: str,
    short_id: str,
    title: str,
    culprit: str,
    level: str,
    platform: str,
    project: str,
    first: datetime,
    last: datetime,
    count: int,
    users: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "shortId": short_id,
        "title": title,
        "culprit": culprit,
        "level": level,
        "status": "unresolved",
        "platform": platform,
        "firstSeen": _iso(first),
        "lastSeen": _iso(last),
        "count": count,
        "userCount": users,
        "permalink": f"https://sentry.io/organizations/acme/issues/{issue_id}/",
        "metadata": metadata,
        "project": {"slug": project, "name": project.title()},
    }


def _ensure_demo_repo(path: Path) -> None:
    if not path.exists():
        logger.warning("Demo repository not found at %s", path)
        return
    try:
        if not (path / ".git").exists():
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        if head.returncode == 0:
            return
        subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Tweaker Demo",
                "-c",
                "user.email=demo@tweaker.local",
                "commit",
                "-q",
                "-m",
                "Initial demo store",
            ],
            cwd=path,
            check=True,
        )
        subprocess.run(["git", "branch", "-M", "main"], cwd=path, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning("Could not initialize demo git repository: %s", exc)


def seed_demo(force: bool = False) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    repo_path = settings.demo_repo_path
    if not repo_path.is_absolute():
        repo_path = (Path.cwd() / repo_path).resolve()
    _ensure_demo_repo(repo_path)

    with session_scope() as session:
        existing = session.scalar(select(Organization).where(Organization.slug == "acme"))
        if existing is not None:
            if not force:
                return
            session.execute(delete(SeerRun).where(SeerRun.org_slug == "acme"))
            session.delete(existing)
            session.commit()

        org = Organization(
            slug="acme",
            name="Acme Commerce",
            sentry_url="https://sentry.io",
            sentry_org_slug="acme",
            ai_enabled=True,
            auto_investigate=True,
            auto_fix=False,
            fixability_threshold=70,
        )
        session.add(org)
        session.flush()
        repo = Repository(
            org_id=org.id,
            name="acme-store",
            provider="local",
            url=str(repo_path),
            default_branch="main",
            local_path=str(repo_path),
            project_slugs=["backend", "frontend"],
            test_command="python -m pytest tests -q",
        )
        session.add(repo)
        session.flush()
        org.default_repo_id = repo.id
        session.commit()

        # --- Issue 1: coupon KeyError (autofix demo) --------------------------
        checkout_event = _event(
            event_id="a1b2c3d4e5f60718",
            exc_type="KeyError",
            exc_value="'SPRING25'",
            platform="python",
            culprit="app.checkout.apply_discount",
            created=now - timedelta(minutes=12),
            frames=[
                _frame(
                    "app/api.py",
                    "checkout",
                    64,
                    context_line="    return final_total(order, payload.get('coupon'))",
                    pre=["def checkout(payload):", "    order = build_order(payload)"],
                    post=["", "def build_order(payload):"],
                    module="app.api",
                ),
                _frame(
                    "app/checkout.py",
                    "final_total",
                    13,
                    context_line="        return apply_discount(order, code)",
                    pre=["def final_total(order, code=None):", "    if code:"],
                    post=["    return order[\"total\"]", ""],
                    module="app.checkout",
                ),
                _frame(
                    "app/checkout.py",
                    "apply_discount",
                    7,
                    context_line="    discount = DISCOUNTS[code]",
                    pre=["DISCOUNTS = {", '    "WELCOME10": 0.10,', "}", "", "def apply_discount(order, code):"],
                    post=['    return order["total"] * (1 - discount)', "", "", "def final_total(order, code=None):"],
                    module="app.checkout",
                ),
            ],
            tags={
                "environment": "production",
                "release": "store@1.8.2",
                "level": "error",
                "transaction": "POST /api/checkout",
                "runtime": "CPython 3.12.4",
                "server_name": "web-3",
            },
            breadcrumbs=[
                {"timestamp": _iso(now - timedelta(minutes=13)), "category": "http", "message": "POST /api/checkout 500"},
                {"timestamp": _iso(now - timedelta(minutes=12)), "category": "query", "message": "SELECT coupon FROM campaigns WHERE code = 'SPRING25'"},
                {"timestamp": _iso(now - timedelta(minutes=12)), "category": "log", "message": "Coupon SPRING25 not found in discount table"},
            ],
            extra={"order_id": "ord_9912", "coupon": "SPRING25", "cart_total": 184.0},
        )
        issue1 = upsert_issue(
            session,
            org,
            _issue_payload(
                issue_id="1001",
                short_id="BACKEND-1A2B",
                title="KeyError: 'SPRING25'",
                culprit="app.checkout.apply_discount",
                level="error",
                platform="python",
                project="backend",
                first=now - timedelta(days=2),
                last=now - timedelta(minutes=12),
                count=1284,
                users=412,
                metadata={"type": "KeyError", "value": "'SPRING25'", "filename": "app/checkout.py", "function": "apply_discount"},
            ),
            event=checkout_event,
        )
        issue1.repo_id = repo.id
        issue1.fixability_score = 92
        issue1.fixability_reason = "Strong application stack trace and a local repository make this a high-confidence autofix candidate."
        issue1.fixability_signals = ["3 in-application frames", "Source line context is present in Sentry", "Repository source context is available"]
        issue1.scanned_at = now - timedelta(minutes=10)

        # --- Issue 2: frontend undefined product ------------------------------
        frontend_event = _event(
            event_id="b2c3d4e5f6071829",
            exc_type="TypeError",
            exc_value="Cannot read properties of undefined (reading 'id')",
            platform="javascript",
            culprit="cartTotal (web/src/cart.tsx)",
            created=now - timedelta(hours=3),
            frames=[
                _frame("web/src/cart.tsx", "cartTotal", 10, context_line="    const price = item.product.id ? 10 : 0;", pre=["export function cartTotal(items: CartItem[]): number {", "  return items.reduce((total, item) => {"], post=["    return total + price;", "  }, 0);"], module="web/src/cart"),
                _frame("web/src/CartPage.tsx", "CartPage", 28, context_line="  const total = cartTotal(cart.items);", pre=["export function CartPage() {", "  const cart = useCart();"], post=["  return <CartSummary total={total} />;", "}"], module="web/src/CartPage"),
            ],
            tags={"environment": "production", "release": "store@1.8.2", "level": "error", "browser": "Chrome 126", "transaction": "/cart"},
            breadcrumbs=[
                {"timestamp": _iso(now - timedelta(hours=3)), "category": "navigation", "message": "Navigated to /cart"},
                {"timestamp": _iso(now - timedelta(hours=3)), "category": "ui.click", "message": "Removed product from cart"},
            ],
            extra={"item_id": "item_771", "cart_size": 3},
            request={"url": "https://shop.acme.test/cart", "method": "GET"},
        )
        issue2 = upsert_issue(
            session,
            org,
            _issue_payload(
                issue_id="1002",
                short_id="FRONTEND-7C3D",
                title="TypeError: Cannot read properties of undefined (reading 'id')",
                culprit="cartTotal (web/src/cart.tsx)",
                level="error",
                platform="javascript",
                project="frontend",
                first=now - timedelta(days=1),
                last=now - timedelta(hours=3),
                count=386,
                users=129,
                metadata={"type": "TypeError", "value": "Cannot read properties of undefined (reading 'id')", "filename": "web/src/cart.tsx", "function": "cartTotal"},
            ),
            event=frontend_event,
        )
        issue2.repo_id = repo.id
        issue2.fixability_score = 64
        issue2.fixability_reason = "The fault is in application code, but the optional product shape needs a product decision about fallback pricing."
        issue2.fixability_signals = ["2 in-application frames", "Source line context is present in Sentry"]

        # --- Issue 3: profile None -------------------------------------------
        account_event = _event(
            event_id="c3d4e5f60718293a",
            exc_type="AttributeError",
            exc_value="'NoneType' object has no attribute 'address'",
            platform="python",
            culprit="app.accounts.shipping_address",
            created=now - timedelta(hours=8),
            frames=[
                _frame("app/orders.py", "create_shipment", 41, context_line="    address = shipping_address(user)", pre=["def create_shipment(user, order):"], post=["    return carrier.create(address, order)"], module="app.orders"),
                _frame("app/accounts.py", "shipping_address", 9, context_line="    return user.profile.address", pre=["class User:", "    def __init__(self, user_id, email):"], post=["", "", "def display_name(user):"], module="app.accounts"),
            ],
            tags={"environment": "production", "release": "store@1.8.2", "level": "error", "transaction": "POST /api/orders"},
            breadcrumbs=[{"timestamp": _iso(now - timedelta(hours=8)), "category": "query", "message": "Loaded user u_8421 without profile"}],
            extra={"user_id": "u_8421", "order_id": "ord_8123"},
        )
        issue3 = upsert_issue(
            session,
            org,
            _issue_payload(
                issue_id="1003",
                short_id="BACKEND-9F4E",
                title="AttributeError: 'NoneType' object has no attribute 'address'",
                culprit="app.accounts.shipping_address",
                level="error",
                platform="python",
                project="backend",
                first=now - timedelta(days=4),
                last=now - timedelta(hours=8),
                count=214,
                users=87,
                metadata={"type": "AttributeError", "value": "'NoneType' object has no attribute 'address'", "filename": "app/accounts.py", "function": "shipping_address"},
            ),
            event=account_event,
        )
        issue3.repo_id = repo.id
        issue3.fixability_score = 78
        issue3.fixability_reason = "The missing profile is a local invariant violation; the fix is likely a guard or a profile backfill."
        issue3.fixability_signals = ["2 in-application frames", "Source line context is present in Sentry", "Repository source context is available"]

        # --- Issue 4: connection pool (low signal) ---------------------------
        pool_event = _event(
            event_id="d4e5f60718293a4b",
            exc_type="OperationalError",
            exc_value="connection pool exhausted after 30s",
            platform="python",
            culprit="app.db.session",
            created=now - timedelta(days=1, hours=2),
            frames=[
                _frame("app/db.py", "get_session", 22, in_app=False, context_line="    return pool.connect()", pre=["def get_session():"], post=["", "def health():"], module="app.db"),
            ],
            tags={"environment": "production", "release": "store@1.8.2", "level": "warning", "transaction": "GET /health"},
            breadcrumbs=[{"timestamp": _iso(now - timedelta(days=1, hours=2)), "category": "db", "message": "pool size 20, in use 20"}],
            extra={"pool_size": 20, "in_use": 20},
        )
        issue4 = upsert_issue(
            session,
            org,
            _issue_payload(
                issue_id="1004",
                short_id="BACKEND-2B7C",
                title="OperationalError: connection pool exhausted after 30s",
                culprit="app.db.session",
                level="warning",
                platform="python",
                project="backend",
                first=now - timedelta(days=3),
                last=now - timedelta(days=1, hours=2),
                count=94,
                users=0,
                metadata={"type": "OperationalError", "value": "connection pool exhausted after 30s", "filename": "app/db.py", "function": "get_session"},
            ),
            event=pool_event,
        )
        issue4.repo_id = repo.id
        issue4.fixability_score = 28
        issue4.fixability_reason = "Infrastructure saturation is not a local code defect; investigate pool sizing and traffic first."
        issue4.fixability_signals = ["No in-application frames", "Failure looks environmental or network-related"]

        # --- Issue 5: payment timeout ----------------------------------------
        timeout_event = _event(
            event_id="e5f60718293a4b5c",
            exc_type="TimeoutError",
            exc_value="Stripe request timed out after 10s",
            platform="python",
            culprit="app.payments.charge",
            created=now - timedelta(days=2),
            frames=[
                _frame("app/payments.py", "charge", 58, in_app=False, context_line="    return stripe.Charge.create(**payload)", pre=["def charge(order, token):"], post=["", "def refund(charge_id):"], module="app.payments"),
            ],
            tags={"environment": "production", "release": "store@1.8.2", "level": "error", "transaction": "POST /api/pay"},
            breadcrumbs=[{"timestamp": _iso(now - timedelta(days=2)), "category": "http", "message": "POST api.stripe.com/v1/charges timed out"}],
            extra={"provider": "stripe", "latency_ms": 10000},
        )
        issue5 = upsert_issue(
            session,
            org,
            _issue_payload(
                issue_id="1005",
                short_id="BACKEND-5D8A",
                title="TimeoutError: Stripe request timed out after 10s",
                culprit="app.payments.charge",
                level="error",
                platform="python",
                project="backend",
                first=now - timedelta(days=6),
                last=now - timedelta(days=2),
                count=61,
                users=48,
                metadata={"type": "TimeoutError", "value": "Stripe request timed out after 10s", "filename": "app/payments.py", "function": "charge"},
            ),
            event=timeout_event,
        )
        issue5.repo_id = repo.id
        issue5.fixability_score = 34
        issue5.fixability_reason = "Third-party latency is not directly fixable; add retries, idempotency, or a timeout budget."
        issue5.fixability_signals = ["All frames are third-party or system code", "Failure looks environmental or network-related"]

        # --- A completed demo investigation ---------------------------------
        run = Run(
            org_id=org.id,
            issue_id=issue1.id,
            repo_id=repo.id,
            kind="investigate",
            status="succeeded",
            trigger="manual",
            provider="demo",
            model="tweaker-heuristic-v1",
            summary="A coupon code that is not present in the discount table raises an uncaught KeyError.",
            root_cause=(
                "`apply_discount()` indexes `DISCOUNTS` with the submitted coupon code. Expired or "
                "typo'd codes are treated as a programming error instead of a validation failure, so "
                "checkout returns a 500."
            ),
            confidence=0.93,
            fixability_score=92,
            patch=_DEMO_PATCH,
            branch=None,
            run_metadata={
                "evidence": [
                    {"file": "app/checkout.py", "line": 7, "explanation": "Direct dictionary indexing raises KeyError for unknown coupon codes."}
                ],
                "fix_plan": [
                    "Treat an unknown coupon as a zero-value discount.",
                    "Add the missing SPRING25 campaign to the discount table.",
                    "Add a regression test for unknown and expired codes.",
                ],
                "tests": ["python -m pytest tests -q"],
                "risk": "low",
                "needs_human": False,
                "files": ["app/api.py", "app/checkout.py"],
            },
            started_at=now - timedelta(minutes=9),
            finished_at=now - timedelta(minutes=8),
        )
        session.add(run)
        session.flush()
        steps = [
            ("queue", "Queued investigation", "succeeded", "Tweaker accepted the run and is gathering context.", {}),
            ("context", "Collecting Sentry context", "succeeded", "Loaded BACKEND-1A2B, latest event, and 1 suspect commit.", {}),
            ("scan", "Scoring fixability", "succeeded", "Fixability 92/100 — strong application stack trace and source context.", {"score": 92}),
            ("code", "Locating relevant source", "succeeded", "Read 3 source snippets from 2 files.", {"files": ["app/api.py", "app/checkout.py"]}),
            ("analysis", "Analyzing root cause", "succeeded", "Unknown coupon codes reach direct dictionary indexing.", {"confidence": 0.93, "risk": "low"}),
            ("patch", "Validating proposed fix", "succeeded", "Patch applies cleanly to app/checkout.py.", {"files": ["app/checkout.py"]}),
        ]
        for seq, (kind, title, status, body, data) in enumerate(steps, 1):
            session.add(
                RunStep(
                    run_id=run.id,
                    seq=seq,
                    kind=kind,
                    title=title,
                    status=status,
                    body=body,
                    data=data,
                    created_at=now - timedelta(minutes=9) + timedelta(seconds=seq * 8),
                )
            )

        # A completed Seer-contract run, so the operator console has a real
        # example of the blocks Sentry receives.
        demo_patches = unified_diff_to_patches(_DEMO_PATCH, "acme/store")
        demo_sentry_run_id = str(uuid.uuid4())
        session.add(
            SeerRun(
                uuid=str(uuid.uuid4()),
                org_id=1,
                org_slug="acme",
                project_id=2,
                group_id=1001,
                feature_id="autofix",
                kind="feature",
                status="completed",
                title="Autofix — BACKEND-1A2B",
                blocks=[
                    {
                        "id": uuid.uuid4().hex,
                        "message": {
                            "role": "assistant",
                            "content": "A coupon code that is not present in the discount table raises an uncaught KeyError.",
                            "metadata": {"step": "root_cause", "referrer": "api.web"},
                        },
                        "timestamp": _iso(now - timedelta(minutes=8)),
                        "loading": False,
                        "artifacts": [
                            {
                                "key": "root_cause",
                                "data": {
                                    "one_line_description": "A coupon code that is not present in the discount table raises an uncaught KeyError.",
                                    "five_whys": [
                                        "`apply_discount()` indexes `DISCOUNTS` directly.",
                                        "Unknown coupon codes are treated as a programming error.",
                                        "Checkout returns a 500 instead of ignoring the invalid code.",
                                    ],
                                    "reproduction_steps": ["POST /api/checkout with coupon=SPRING25"],
                                    "cause": "Direct dictionary indexing in `apply_discount`.",
                                },
                                "reason": "Root cause analysis generated from Sentry telemetry and repository context.",
                            }
                        ],
                    },
                    {
                        "id": uuid.uuid4().hex,
                        "message": {
                            "role": "assistant",
                            "content": "Treat unknown coupons as a zero-value discount and add the campaign.",
                            "metadata": {"step": "solution", "referrer": "api.web"},
                        },
                        "timestamp": _iso(now - timedelta(minutes=7)),
                        "loading": False,
                        "artifacts": [
                            {
                                "key": "solution",
                                "data": {
                                    "one_line_summary": "Treat unknown coupons as a zero-value discount and add the campaign.",
                                    "steps": [
                                        {"title": "Guard the lookup", "description": "Use DISCOUNTS.get(code, 0.0)."},
                                        {"title": "Add campaign", "description": "Add SPRING25 to the discount table."},
                                        {"title": "Regress", "description": "Add a test for unknown and expired codes."},
                                    ],
                                },
                                "reason": "Remediation plan derived from the root cause.",
                            }
                        ],
                    },
                    {
                        "id": uuid.uuid4().hex,
                        "message": {
                            "role": "assistant",
                            "content": "Patch validated against app/checkout.py.",
                            "metadata": {"step": "code_changes", "referrer": "api.web"},
                        },
                        "timestamp": _iso(now - timedelta(minutes=6)),
                        "loading": False,
                        "artifacts": [],
                        "file_patches": demo_patches,
                        "merged_file_patches": demo_patches,
                    },
                ],
                run_metadata={
                    "feature_id": "autofix",
                    "group_id": 1001,
                    "referrer": "api.web",
                    "owner_user_id": 42,
                    "summary": "A coupon code that is not present in the discount table raises an uncaught KeyError.",
                    "root_cause": "Direct dictionary indexing in `apply_discount`.",
                    "confidence": 0.93,
                    "repos": [
                        {
                            "provider": "github",
                            "owner": "acme",
                            "name": "store",
                            "external_id": "42",
                            "branch_name": "main",
                        }
                    ],
                    "issue": {
                        "short_id": "BACKEND-1A2B",
                        "title": "KeyError: 'SPRING25'",
                        "permalink": "https://sentry.io/organizations/acme/issues/1001/",
                    },
                },
                repo_pr_states={},
                coding_agents={},
                queued_feedback=[],
                usage={"usages": [{"completion_tokens": 612, "prompt_tokens": 8100, "total_tokens": 8712, "dollar_cost": 0.0, "model": "tweaker-heuristic-v1"}]},
                payload={
                    "group_id": 1001,
                    "project_id": 2,
                    "short_id": "BACKEND-1A2B",
                    "title": "KeyError: 'SPRING25'",
                    "culprit": "app.checkout.apply_discount",
                    "step": "code_changes",
                    "referrer": "api.web",
                },
                referrer="api.web",
                sentry_run_id=demo_sentry_run_id,
                external_idempotency_key=demo_sentry_run_id,
            )
        )

        # A short sample Ask conversation makes the dashboard feel alive.
        session.add(
            AskConversation(
                org_id=org.id,
                title="What should on-call look at first?",
                messages=[
                    {"role": "user", "content": "What should on-call look at first?"},
                    {
                        "role": "assistant",
                        "content": (
                            "Start with BACKEND-1A2B. It has the highest volume (1,284 events / 412 users), "
                            "is still unresolved, and has a high-confidence fix: unknown coupon codes hit a "
                            "direct dictionary index in `apply_discount`. BACKEND-9F4E is next because the "
                            "missing profile is a local invariant that can be guarded."
                        ),
                        "citations": ["BACKEND-1A2B", "BACKEND-9F4E"],
                    },
                ],
            )
        )
        session.add(
            AuditEvent(
                org_id=org.id,
                actor="system",
                action="demo.seeded",
                target_type="org",
                target_id=org.id,
                event_metadata={"issues": 5, "repositories": 1},
            )
        )
        session.commit()
        logger.info("Seeded demo organization acme with 5 issues, 1 local investigation, and 1 Seer run")
