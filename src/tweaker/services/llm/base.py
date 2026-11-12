from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd


@dataclass(slots=True)
class StructuredResult(Generic[T]):
    value: T
    usage: Usage


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolTurn:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw_content: Any = None
    stop_reason: str = ""


class LLMProvider:
    name = "base"
    model = ""

    @property
    def available(self) -> bool:
        return False

    def describe(self) -> dict[str, str | bool]:
        return {"provider": self.name, "model": self.model, "available": self.available}

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 8000,
    ) -> StructuredResult[T]:
        raise NotImplementedError

    def complete_text(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
    ) -> tuple[str, Usage]:
        raise NotImplementedError

    def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int = 8000,
    ) -> ToolTurn:
        raise NotImplementedError
