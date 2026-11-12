from __future__ import annotations

import argparse
import os
import sys

from .config import get_settings


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "tweaker.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )


def _worker(args: argparse.Namespace) -> None:
    from .worker import main

    main()


def _init_db(args: argparse.Namespace) -> None:
    from .db import init_db

    init_db()
    print("Database initialized.")


def _seed_demo(args: argparse.Namespace) -> None:
    os.environ["TWEAKER_DEMO_MODE"] = "true"
    get_settings.cache_clear()
    from .db import init_db
    from .seed import seed_demo

    init_db()
    seed_demo(force=args.force)
    print("Demo data is ready.")


def _index_repos(args: argparse.Namespace) -> None:
    from .services.code_index import index_all

    result = index_all(full=args.full)
    print(result)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tweaker", description="Drop-in Seer service for self-hosted Sentry")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the Seer API and operator console")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    worker = sub.add_parser("worker", help="Run the background Seer worker")
    worker.set_defaults(func=_worker)

    init = sub.add_parser("init-db", help="Create database tables")
    init.set_defaults(func=_init_db)

    seed = sub.add_parser("seed-demo", help="Seed demo data for the operator console")
    seed.add_argument("--force", action="store_true", help="Replace existing demo data")
    seed.set_defaults(func=_seed_demo)

    index = sub.add_parser("index-repos", help="Build the local code index")
    index.add_argument("--full", action="store_true", help="Reindex every file")
    index.set_defaults(func=_index_repos)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
