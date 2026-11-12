from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    file: str = Field(default="", description="Repository-relative file path, or empty if not code-specific")
    line: int | None = Field(default=None, description="1-based line number if known")
    explanation: str = Field(description="Why this evidence matters")


class RepoPatch(BaseModel):
    repo: str = Field(
        description="Repository in owner/name form, exactly as supplied in the repository context"
    )
    patch: str = Field(description="Unified diff for this repository")


class Diagnosis(BaseModel):
    summary: str = Field(description="One or two sentence explanation of the failure")
    root_cause: str = Field(description="Specific root cause, including the faulty code path")
    confidence: float = Field(ge=0, le=1, description="Confidence from 0 to 1")
    evidence: list[Evidence] = Field(default_factory=list)
    five_whys: list[str] = Field(
        default_factory=list,
        description="Ordered causal chain from symptom to root cause (2-5 entries)",
    )
    reproduction_steps: list[str] = Field(
        default_factory=list,
        description="Steps that reproduce the failure from the supplied evidence",
    )
    fix_plan: list[str] = Field(default_factory=list, description="Ordered implementation steps")
    patch: str = Field(default="", description="Unified diff against the primary repository, or empty string")
    patches: list[RepoPatch] = Field(
        default_factory=list,
        description="One patch per repository when the fix spans multiple repositories",
    )
    tests: list[str] = Field(default_factory=list, description="Tests or checks that should validate the fix")
    risk: Literal["low", "medium", "high"] = "medium"
    needs_human: bool = Field(default=True, description="True when a human should review assumptions")


class FixabilityAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    reason: str
    signals: list[str] = Field(default_factory=list)
    likely_files: list[str] = Field(default_factory=list)


class AskAnswer(BaseModel):
    answer: str = Field(description="Answer grounded in the supplied Sentry context")
    citations: list[str] = Field(
        default_factory=list,
        description="Issue identifiers or URLs used as evidence, e.g. BACKEND-1A2B",
    )
    follow_ups: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
