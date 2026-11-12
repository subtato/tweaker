from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .llm import ProviderError, get_provider
from .llm.prompts import FIXABILITY_SYSTEM, build_fixability_prompt
from .llm.schema import FixabilityAssessment
from .redaction import compact_event, in_app_frames

logger = logging.getLogger(__name__)

_FIXABLE_ERRORS = {
    "AttributeError",
    "IndexError",
    "KeyError",
    "LogicError",
    "NameError",
    "NullPointerException",
    "RangeError",
    "ReferenceError",
    "TypeError",
    "UnhandledPromiseRejection",
    "ValueError",
    "ZeroDivisionError",
}
_LOW_SIGNAL = {
    "ECONNREFUSED",
    "ETIMEDOUT",
    "ENOTFOUND",
    "TimeoutError",
    "ConnectionError",
    "NetworkError",
    "503",
    "502",
    "504",
    "OOMKilled",
}
_BUNDLE_MARKERS = ("webpack://", "node_modules/", ".min.js", "vendor/", "site-packages/")


@dataclass(slots=True)
class Fixability:
    score: int
    reason: str
    signals: list[str] = field(default_factory=list)
    likely_files: list[str] = field(default_factory=list)
    source: str = "heuristic"


def heuristic_fixability(issue: Any, event: dict[str, Any], code_context: str = "") -> Fixability:
    compact = compact_event(event, max_chars=100_000) if event else {}
    frames = in_app_frames(compact) if compact else []
    exceptions = compact.get("exceptions") or []
    error_types = " ".join(str(exc.get("type") or "") for exc in exceptions)
    message = " ".join(str(exc.get("value") or "") for exc in exceptions)
    all_frames = [
        frame
        for exc in exceptions
        for frame in (exc.get("stacktrace", {}).get("frames") or [])
    ]
    files = [frame.get("filename") or "" for frame in frames]
    score = 20
    signals: list[str] = []

    if frames:
        score += 25
        signals.append(f"{len(frames)} in-application frame(s)")
    if any(frame.get("contextLine") for frame in frames):
        score += 10
        signals.append("Source line context is present in Sentry")
    if any(re.search(r"\.(py|js|jsx|ts|tsx|go|rb|php|java|kt|cs|rs|ex|exs)$", f) for f in files):
        score += 10
        signals.append("Frames point to source files")
    if any(error in error_types for error in _FIXABLE_ERRORS):
        score += 12
        signals.append("Error type commonly caused by application logic")
    if message and len(message) < 500:
        score += 4
    if code_context and "No source context" not in code_context and len(code_context) > 200:
        score += 15
        signals.append("Repository source context is available")
    if issue is not None and getattr(issue, "repo_id", None):
        score += 5
        signals.append("Repository is mapped to this project")
    if issue is not None and getattr(issue, "issue_metadata", None):
        score += 3

    if not frames:
        score -= 35
        signals.append("No in-application frames")
    if all_frames and all(not frame.get("inApp") for frame in all_frames):
        score -= 15
        signals.append("All frames are third-party or system code")
    if any(marker in " ".join(files) for marker in _BUNDLE_MARKERS):
        score -= 22
        signals.append("Minified or vendored code detected")
    if any(token in error_types or token in message for token in _LOW_SIGNAL):
        score -= 18
        signals.append("Failure looks environmental or network-related")
    if not code_context and not frames:
        score -= 10

    score = max(0, min(100, score))
    if score >= 75:
        reason = "Strong application stack trace and source context make this a good autofix candidate."
    elif score >= 55:
        reason = "The issue is probably fixable, but some context is missing or ambiguous."
    elif score >= 35:
        reason = "Partial context is available; investigation may help but a code fix is uncertain."
    else:
        reason = "Insufficient or low-signal context for a safe automated code fix."
    likely = []
    for path in files:
        if path and path not in likely:
            likely.append(path)
    return Fixability(
        score=score,
        reason=reason,
        signals=signals,
        likely_files=likely[:8],
    )


def assess_fixability(
    issue: Any,
    event: dict[str, Any],
    code_context: str = "",
    *,
    use_llm: bool = True,
    org: Any | None = None,
) -> Fixability:
    base = heuristic_fixability(issue, event, code_context)
    if not use_llm:
        return base
    provider = get_provider(org)
    if provider.name == "demo":
        return base
    try:
        result = provider.complete_structured(
            system=FIXABILITY_SYSTEM,
            prompt=build_fixability_prompt(issue, event, code_context),
            schema=FixabilityAssessment,
            max_tokens=1200,
        )
        assessment = result.value
        # Blend the model judgement with deterministic signals. This prevents a
        # confident but under-informed classifier from bypassing guardrails.
        blended = round(assessment.score * 0.65 + base.score * 0.35)
        signals = list(dict.fromkeys([*assessment.signals, *base.signals]))
        return Fixability(
            score=max(0, min(100, blended)),
            reason=assessment.reason,
            signals=signals,
            likely_files=assessment.likely_files or base.likely_files,
            source="llm+heuristic",
        )
    except ProviderError as exc:
        logger.warning("Fixability LLM failed; using heuristic: %s", exc)
        return base
    except Exception as exc:
        logger.exception("Unexpected fixability failure: %s", exc)
        return base


def should_auto_investigate(org: Any, fixability: Fixability) -> bool:
    return bool(
        getattr(org, "auto_investigate", False)
        and fixability.score >= getattr(org, "fixability_threshold", 70)
    )
