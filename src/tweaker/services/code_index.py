from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import CodeIndexEntry, Repository
from .codebase import MAX_FILE_BYTES, RepositoryWorkspace

logger = logging.getLogger(__name__)

_SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
    re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
    re.compile(r"\b(?:function|func)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE),
    re.compile(r"\btype\s+([A-Za-z_][A-Za-z0-9_]*)\s+struct", re.MULTILINE),
]


def extract_symbols(content: str) -> list[str]:
    symbols: list[str] = []
    for pattern in _SYMBOL_PATTERNS:
        for match in pattern.finditer(content):
            value = match.group(1)
            if value not in symbols:
                symbols.append(value)
            if len(symbols) >= 200:
                return symbols
    return symbols


def index_repository(repo: Repository, *, full: bool = False) -> dict[str, Any]:
    return index_workspace(RepositoryWorkspace(repo), repo.id, full=full)


def index_workspace(
    workspace: RepositoryWorkspace, repo_key: str, *, full: bool = False
) -> dict[str, Any]:
    root = workspace.ensure()
    indexed = 0
    skipped = 0
    seen: set[str] = set()
    with session_scope() as session:
        existing = {
            entry.path: entry
            for entry in session.scalars(
                select(CodeIndexEntry).where(CodeIndexEntry.repo_id == repo_key)
            )
        }
        for path in workspace._walk_text_files():
            try:
                stat = path.stat()
                if stat.st_size > MAX_FILE_BYTES:
                    skipped += 1
                    continue
                relative = path.relative_to(root).as_posix()
                seen.add(relative)
                previous = existing.get(relative)
                if previous is not None and not full and abs(previous.mtime - stat.st_mtime) < 0.001:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped += 1
                continue
            symbols = extract_symbols(content)
            if previous is None:
                session.add(
                    CodeIndexEntry(
                        repo_id=repo_key,
                        path=relative,
                        language=path.suffix.lower().lstrip("."),
                        content=content[:500_000],
                        symbols=" ".join(symbols),
                        line_count=content.count("\n") + 1,
                        mtime=stat.st_mtime,
                    )
                )
            else:
                previous.language = path.suffix.lower().lstrip(".")
                previous.content = content[:500_000]
                previous.symbols = " ".join(symbols)
                previous.line_count = content.count("\n") + 1
                previous.mtime = stat.st_mtime
            indexed += 1
        stale = [path for path in existing if path not in seen]
        if stale:
            session.execute(
                delete(CodeIndexEntry).where(
                    CodeIndexEntry.repo_id == repo_key,
                    CodeIndexEntry.path.in_(stale),
                )
            )
        session.commit()
    return {"repo_id": repo_key, "indexed": indexed, "skipped": skipped, "removed": len(stale)}


def index_all(*, full: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        repos = list(session.scalars(select(Repository).where(Repository.enabled.is_(True))))
    results = []
    for repo in repos:
        try:
            results.append(index_repository(repo, full=full))
        except Exception as exc:
            logger.warning("Could not index repo %s: %s", repo.id, exc)
            results.append({"repo_id": repo.id, "error": str(exc)})
    return {"repositories": results}


def search_index(
    session: Session,
    *,
    repo_ids: list[str],
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    tokens = [token for token in re.findall(r"[A-Za-z0-9_.:-]{2,}", query)][:8]
    if not tokens or not repo_ids:
        return []
    clauses = []
    for token in tokens:
        pattern = f"%{token}%"
        clauses.extend(
            [
                CodeIndexEntry.content.ilike(pattern),
                CodeIndexEntry.symbols.ilike(pattern),
                CodeIndexEntry.path.ilike(pattern),
            ]
        )
    rows = list(
        session.scalars(
            select(CodeIndexEntry)
            .where(CodeIndexEntry.repo_id.in_(repo_ids), or_(*clauses))
            .limit(max(limit * 5, 25))
        )
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        line = _best_line(row.content, tokens)
        results.append(
            {
                "repo_id": row.repo_id,
                "path": row.path,
                "line": line[0],
                "content": line[1],
                "symbols": row.symbols.split()[:20],
                "language": row.language,
            }
        )
        if len(results) >= limit:
            break
    return results


def _best_line(content: str, tokens: list[str]) -> tuple[int, str]:
    lines = content.splitlines()
    lowered = [token.lower() for token in tokens]
    best = (1, lines[0] if lines else "")
    best_score = 0
    for index, line in enumerate(lines[:5000], 1):
        score = sum(1 for token in lowered if token in line.lower())
        if score > best_score:
            best = (index, line.strip()[:500])
            best_score = score
            if score == len(lowered):
                break
    return best


def index_stats(session: Session, repo_id: str | None = None) -> dict[str, Any]:
    from sqlalchemy import func

    query = select(
        func.count(CodeIndexEntry.id),
        func.coalesce(func.sum(CodeIndexEntry.line_count), 0),
    )
    if repo_id:
        query = query.where(CodeIndexEntry.repo_id == repo_id)
    files, lines = session.execute(query).one()
    return {"files": int(files or 0), "lines": int(lines or 0)}
