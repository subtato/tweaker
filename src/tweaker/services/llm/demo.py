from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel

from .base import LLMProvider, StructuredResult, ToolTurn, Usage
from .schema import AskAnswer, Diagnosis, FixabilityAssessment

T = TypeVar("T", bound=BaseModel)

_DEMO_PATCH = (
    "--- a/app/checkout.py\n"
    "+++ b/app/checkout.py\n"
    "@@ -1,8 +1,9 @@\n"
    " DISCOUNTS = {\n"
    '     "WELCOME10": 0.10,\n'
    '+    "SPRING25": 0.25,\n'
    " }\n"
    " \n"
    " \n"
    " def apply_discount(order, code):\n"
    "-    discount = DISCOUNTS[code]\n"
    "+    discount = DISCOUNTS.get(code, 0.0)\n"
    '     return order["total"] * (1 - discount)\n'
)


class DemoProvider(LLMProvider):
    """Deterministic local analyzer used for demos, tests, and air-gapped setups."""

    name = "demo"
    model = "tweaker-heuristic-v1"

    @property
    def available(self) -> bool:
        return True

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 8000,
    ) -> StructuredResult[T]:
        if schema is Diagnosis:
            value: BaseModel = self._diagnosis(prompt)
        elif schema is FixabilityAssessment:
            value = self._fixability(prompt)
        elif schema is AskAnswer:
            value = self._answer(prompt)
        else:
            raise ValueError(f"Demo provider does not support {schema.__name__}")
        return StructuredResult(value=value, usage=Usage())  # type: ignore[arg-type]

    def complete_text(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
    ) -> tuple[str, Usage]:
        return self._diagnosis(prompt).summary, Usage()

    def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list,
        max_tokens: int = 8000,
    ) -> ToolTurn:
        prompt = "\n".join(str(item.get("content") or "") for item in messages[-3:])
        return ToolTurn(content=self._diagnosis(prompt).summary)

    def _diagnosis(self, prompt: str) -> Diagnosis:
        exception = _first(r"(?:Exceptions:\n- )([^\n]+)", prompt) or "Unhandled exception"
        frame = _first(r"#1 ([^\n]+?):(\d+) in ([^\n]+)", prompt)
        file_path = ""
        line_no: int | None = None
        function = ""
        if frame:
            location, line, function = frame
            file_path = location.split(" ")[0]
            line_no = int(line)
        has_checkout = "DISCOUNTS[code]" in prompt and "apply_discount" in prompt
        if has_checkout:
            return Diagnosis(
                summary="A coupon code that is not present in the discount table raises an uncaught KeyError.",
                root_cause=(
                    "`apply_discount()` indexes `DISCOUNTS` with the submitted coupon code. "
                    "Expired or typo'd codes are therefore treated as a programming error instead of "
                    "a normal validation failure, so checkout returns a 500."
                ),
                confidence=0.93,
                evidence=[
                    {
                        "file": "app/checkout.py",
                        "line": 7,
                        "explanation": "Direct dictionary indexing raises KeyError for unknown coupon codes.",
                    }
                ],
                fix_plan=[
                    "Treat an unknown coupon as a zero-value discount.",
                    "Add the missing SPRING25 campaign to the discount table.",
                    "Add a regression test for unknown and expired codes.",
                ],
                patch=_DEMO_PATCH,
                tests=["pytest tests/test_checkout.py -q"],
                risk="low",
                needs_human=False,
            )
        if file_path:
            return Diagnosis(
                summary=f"{exception} originates in {file_path}:{line_no} ({function}).",
                root_cause=(
                    f"The latest in-application frame is `{function}` in `{file_path}` at line {line_no}. "
                    "The available context is enough to locate the failure, but the exact fix requires "
                    "reviewing the surrounding invariants."
                ),
                confidence=0.58,
                evidence=[
                    {
                        "file": file_path,
                        "line": line_no,
                        "explanation": f"Most recent in-app frame: {function}.",
                    }
                ],
                fix_plan=[
                    f"Inspect {file_path}:{line_no} and the inputs reaching {function}.",
                    "Add a guard or validation at the boundary that allows the invalid state.",
                    "Add a regression test using the event's tag and request context.",
                ],
                patch="",
                tests=["Run the repository's test suite for the affected module."],
                risk="medium",
                needs_human=True,
            )
        return Diagnosis(
            summary=f"{exception} could not be mapped to application source.",
            root_cause=(
                "The event has no in-application frame or repository context. Connect the project's "
                "repository and ensure source maps or debug symbols are uploaded, then rerun the investigation."
            ),
            confidence=0.25,
            evidence=[],
            fix_plan=["Connect the repository for this project.", "Re-run the investigation."],
            patch="",
            tests=[],
            risk="high",
            needs_human=True,
        )

    def _fixability(self, prompt: str) -> FixabilityAssessment:
        in_app = "[in_app]" in prompt
        has_source = "## Source context" in prompt and "No source context" not in prompt
        score = 35 + (30 if in_app else 0) + (20 if has_source else 0)
        if "minified" in prompt.lower() or "webpack" in prompt.lower():
            score -= 25
        score = max(0, min(score, 92))
        signals = []
        if in_app:
            signals.append("Has an in-application stack frame")
        if has_source:
            signals.append("Repository source context is available")
        if score < 50:
            signals.append("Insufficient context to localize the fault")
        return FixabilityAssessment(
            score=score,
            reason=(
                "The stack trace contains application frames and source context."
                if score >= 60
                else "The available telemetry is not specific enough for a safe code fix."
            ),
            signals=signals,
            likely_files=re.findall(r"#\d+ ([^\s:]+):", prompt)[:5],
        )

    def _answer(self, prompt: str) -> AskAnswer:
        short_ids = []
        for match in re.findall(r"### ([A-Z][A-Z0-9]*-\w+)", prompt):
            if match not in short_ids:
                short_ids.append(match)
        if short_ids:
            return AskAnswer(
                answer=(
                    f"I found {len(short_ids)} relevant issue(s): {', '.join(short_ids[:5])}. "
                    "The strongest signal is the most recent issue in the supplied context; open it for "
                    "the full stack trace, tags, and investigation timeline. The demo provider summarizes "
                    "cached Sentry data and does not call an external model."
                ),
                citations=short_ids[:5],
                follow_ups=[
                    "Which issue has the highest event volume?",
                    "Investigate the most recent issue.",
                ],
                confidence=0.55,
            )
        return AskAnswer(
            answer=(
                "No matching issues were found in the local cache. Try a broader Sentry query or sync "
                "issues from the organization first."
            ),
            citations=[],
            follow_ups=["Sync recent issues", "Show unresolved errors from the last 24 hours"],
            confidence=0.2,
        )


def _first(pattern: str, text: str) -> tuple[str, ...] | str | None:
    match = re.search(pattern, text)
    if not match:
        return None
    if len(match.groups()) == 1:
        return match.group(1).strip()
    return tuple(group.strip() for group in match.groups())
