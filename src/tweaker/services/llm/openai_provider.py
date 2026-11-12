from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

import httpx
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


class OpenAICompatibleProvider(LLMProvider):
    """Adapter for OpenAI, Azure-compatible gateways, vLLM, LM Studio and Ollama."""

    name = "openai"

    def __init__(self, base_url: str, api_key: str | None, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self._client = httpx.Client(timeout=httpx.Timeout(300.0, connect=15.0))

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
        except httpx.RequestError as exc:
            raise ProviderError(f"Could not reach the model endpoint: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"Model endpoint returned {response.status_code}: {response.text[:600]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("Model endpoint returned invalid JSON") from exc

    def _usage(self, data: dict[str, Any]) -> Usage:
        usage = data.get("usage") or {}
        return Usage(
            input_tokens=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        )

    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 8000,
    ) -> StructuredResult[T]:
        base_payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        payload = dict(base_payload)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": _strict_schema(schema.model_json_schema()),
            },
        }
        try:
            data = self._request(payload)
        except ProviderError:
            # Many local servers do not implement response_format. Retry with
            # an explicit JSON instruction and parse the content ourselves.
            payload = dict(base_payload)
            payload["messages"] = [
                {"role": "system", "content": system + "\nReturn valid JSON only."},
                {"role": "user", "content": prompt},
            ]
            data = self._request(payload)
        content = _content(data)
        try:
            value = schema.model_validate(_loads_json(content))
        except Exception as exc:
            raise ProviderError(f"Could not parse model JSON response: {exc}") from exc
        return StructuredResult(value=value, usage=self._usage(data))

    def complete_text(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4000,
        model: str | None = None,
    ) -> tuple[str, Usage]:
        data = self._request(
            {
                "model": model or self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.1,
            }
        )
        return _content(data), self._usage(data)


    def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int = 8000,
    ) -> ToolTurn:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openai_messages(system, messages),
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ],
            "tool_choice": "auto",
        }
        data = self._request(payload)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("Model endpoint returned no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            calls.append(
                ToolCall(
                    id=str(call.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        return ToolTurn(
            content=str(content),
            tool_calls=calls,
            usage=self._usage(data),
            stop_reason=str(choice.get("finish_reason") or ""),
        )


def _to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": message.get("content") or None}
            calls = message.get("tool_calls") or []
            if calls:
                item["tool_calls"] = [
                    {
                        "id": call.get("id"),
                        "type": "function",
                        "function": {
                            "name": call.get("name"),
                            "arguments": json.dumps(call.get("arguments") or {}),
                        },
                    }
                    for call in calls
                ]
            result.append(item)
        elif role == "tool":
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id"),
                    "content": str(message.get("content") or ""),
                }
            )
        else:
            result.append({"role": "user", "content": str(message.get("content") or "")})
    return result


def _content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError("Model endpoint returned no choices")
    message = choices[0].get("message") or {}
    if message.get("refusal"):
        raise ProviderError(f"Model refused the request: {message['refusal']}")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        raise ProviderError("Model returned an empty response")
    return str(content)


def _loads_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "additionalProperties" not in schema:
            schema["additionalProperties"] = False
        for value in schema.values():
            _strict_schema(value)
    elif isinstance(schema, list):
        for item in schema:
            _strict_schema(item)
    return schema
