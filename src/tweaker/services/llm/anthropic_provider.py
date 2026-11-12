from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import (
    LLMProvider,
    ProviderError,
    StructuredResult,
    ToolCall,
    ToolSpec,
    ToolTurn,
    Usage,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# USD per million tokens (input, output). Used only for local cost estimates.
_PRICING = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.1, 0.5),
    "claude-haiku-4-5": (1.0, 5.0),
}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "high") -> None:
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self._client: Any | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency is required
                raise ProviderError("The anthropic package is not installed") from exc
            self._client = anthropic.Anthropic(api_key=self.api_key, max_retries=2)
        return self._client

    def _usage(self, response: Any) -> Usage:
        raw = getattr(response, "usage", None)
        input_tokens = int(getattr(raw, "input_tokens", 0) or 0)
        output_tokens = int(getattr(raw, "output_tokens", 0) or 0)
        rates = _PRICING.get(self.model, (0.0, 0.0))
        cost = (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000
        return Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=round(cost, 6))

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 8000,
    ) -> StructuredResult[T]:
        client = self._get_client()
        try:
            response = client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
            parsed = getattr(response, "parsed_output", None)
            if parsed is None:
                raise ProviderError("Anthropic returned no parsed output")
            if getattr(response, "stop_reason", None) == "refusal":
                details = getattr(response, "stop_details", None)
                raise ProviderError(f"Claude refused the request: {details}")
            return StructuredResult(value=parsed, usage=self._usage(response))
        except ProviderError:
            raise
        except Exception as exc:
            # Older SDK versions or deployments may not expose messages.parse.
            # Fall back to a JSON-schema-constrained Messages request.
            logger.debug("messages.parse failed, falling back to create: %s", exc)
            return self._complete_with_schema(
                client=client,
                system=system,
                prompt=prompt,
                schema=schema,
                max_tokens=max_tokens,
            )

    def _complete_with_schema(
        self,
        *,
        client: Any,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int,
    ) -> StructuredResult[T]:
        json_schema = _strict_schema(schema.model_json_schema())
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "format": {"type": "json_schema", "schema": json_schema},
                    "effort": self.effort,
                },
            )
        except Exception as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderError("Claude refused the request")
        text = "".join(
            getattr(block, "text", "") for block in response.content if getattr(block, "type", "") == "text"
        )
        try:
            value = schema.model_validate(json.loads(text))
        except Exception as exc:
            raise ProviderError(f"Could not parse Anthropic JSON response: {exc}") from exc
        return StructuredResult(value=value, usage=self._usage(response))

    def complete_text(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
    ) -> tuple[str, Usage]:
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": self.effort},
            )
        except Exception as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderError("Claude refused the request")
        text = "".join(
            getattr(block, "text", "") for block in response.content if getattr(block, "type", "") == "text"
        )
        return text, self._usage(response)

    def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int = 8000,
    ) -> ToolTurn:
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=_to_anthropic_messages(messages),
                tools=[
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                    }
                    for tool in tools
                ],
                output_config={"effort": self.effort},
            )
        except Exception as exc:
            raise ProviderError(f"Anthropic tool request failed: {exc}") from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderError("Claude refused the request")
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_content: list[dict[str, Any]] = []
        for block in response.content:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(getattr(block, "id", "")),
                        name=str(getattr(block, "name", "")),
                        arguments=getattr(block, "input", {}) or {},
                    )
                )
            try:
                raw_content.append(block.model_dump(exclude_none=True))
            except Exception:
                pass
        return ToolTurn(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage=self._usage(response),
            raw_content=raw_content,
            stop_reason=str(getattr(response, "stop_reason", "") or ""),
        )


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tools() -> None:
        nonlocal pending_tool_results
        if pending_tool_results:
            result.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []

    for message in messages:
        role = message.get("role")
        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id"),
                    "content": str(message.get("content") or ""),
                }
            )
            continue
        flush_tools()
        if role == "assistant":
            raw = message.get("raw_content")
            if raw:
                result.append({"role": "assistant", "content": raw})
                continue
            content: list[dict[str, Any]] = []
            if message.get("content"):
                content.append({"type": "text", "text": str(message["content"])})
            for call in message.get("tool_calls") or []:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id"),
                        "name": call.get("name"),
                        "input": call.get("arguments") or {},
                    }
                )
            result.append({"role": "assistant", "content": content})
        else:
            result.append({"role": "user", "content": str(message.get("content") or "")})
    flush_tools()
    return result


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Add additionalProperties: false to object schemas recursively."""
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "additionalProperties" not in schema:
            schema["additionalProperties"] = False
        for value in schema.values():
            _strict_schema(value)
    elif isinstance(schema, list):
        for item in schema:
            _strict_schema(item)
    return schema
