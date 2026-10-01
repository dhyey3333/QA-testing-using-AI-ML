"""Talking to the model.

Any OpenAI-compatible /chat/completions endpoint works (Ollama, vLLM, llama.cpp,
OpenRouter, Groq, ...) through a plain httpx POST, so switching providers is
three environment variables. The default is a local Ollama.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, replace
from typing import Protocol

import httpx

from .actions import InvalidAction
from .prompts import Context, JudgeContext, agent_messages, judge_messages

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen3-vl:4b-instruct"


class ModelError(RuntimeError):
    """The model could not be reached or answered with something that isn't a chat reply."""


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 60.0

    # `or`, not a getenv default: CI systems set unused inputs to "", which must mean "not set".
    @classmethod
    def from_env(cls) -> ModelConfig:
        return cls(
            base_url=(os.getenv("MODEL_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            model=os.getenv("MODEL_NAME") or DEFAULT_MODEL,
            api_key=os.getenv("MODEL_API_KEY") or "",
            timeout=float(os.getenv("MODEL_TIMEOUT") or 60),
        )

    @classmethod
    def judge_from_env(cls, agent: ModelConfig) -> ModelConfig:
        """The judge can be a different (usually bigger) model: JUDGE_NAME, JUDGE_BASE_URL, JUDGE_API_KEY."""
        return replace(
            agent,
            base_url=(os.getenv("JUDGE_BASE_URL") or agent.base_url).rstrip("/"),
            model=os.getenv("JUDGE_NAME") or agent.model,
            api_key=os.getenv("JUDGE_API_KEY") or agent.api_key,
        )


class Model(Protocol):
    name: str

    def decide(self, context: Context) -> dict:
        """The next action, as parsed JSON. Raise ModelError or InvalidAction."""
        ...

    def judge(self, context: JudgeContext) -> dict:
        """{"checks": [...]} for the final page. Raise ModelError or InvalidAction."""
        ...

    def ask(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        """Any other JSON task (writing specs). Raise ModelError or InvalidAction."""
        ...


class HttpModel:
    def __init__(self, config: ModelConfig, judge_config: ModelConfig | None = None) -> None:
        self.config = config
        self.judge_config = judge_config or config
        self.name = config.model if self.judge_config.model == config.model else f"{config.model} (judge: {self.judge_config.model})"
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self._client = httpx.Client(timeout=max(config.timeout, self.judge_config.timeout))
        self._json_mode: dict[str, bool] = {}

    def close(self) -> None:
        self._client.close()

    def decide(self, context: Context) -> dict:
        system, text, image = agent_messages(context)
        return self._ask(self.config, system, text, image, max_tokens=300)

    def judge(self, context: JudgeContext) -> dict:
        system, text, image = judge_messages(context)
        return self._ask(self.judge_config, system, text, image, max_tokens=900)

    def ask(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        return self._ask(self.judge_config, system, user, None, max_tokens=max_tokens)

    def _ask(self, config: ModelConfig, system: str, text: str, image: bytes | None, max_tokens: int) -> dict:
        content: list[dict] = [{"type": "text", "text": text}]
        if image is not None:
            encoded = base64.b64encode(image).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        body: dict = {
            "model": config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0.1,  # a control decision, not creative writing
            "max_tokens": max_tokens,
        }
        json_mode = self._json_mode.get(config.base_url, True)
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        response = self._post(config, body)
        if response.status_code == 400 and json_mode:
            # Some servers reject response_format outright. parse_json_object copes
            # without it, so drop it for this endpoint instead of failing.
            self._json_mode[config.base_url] = False
            del body["response_format"]
            response = self._post(config, body)
        if response.status_code >= 400:
            raise ModelError(f"HTTP {response.status_code} from {config.base_url}: {response.text[:200]}")

        try:
            payload = response.json()
            message = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("the reply was not an OpenAI-style chat completion") from exc
        usage = payload.get("usage") or {}
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        return parse_json_object(message or "")

    def _post(self, config: ModelConfig, body: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        try:
            return self._client.post(f"{config.base_url}/chat/completions", json=body, headers=headers,
                                     timeout=config.timeout)
        except httpx.HTTPError as exc:
            raise ModelError(f"could not reach {config.base_url} ({type(exc).__name__})") from exc


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_json_object(raw: str) -> dict:
    """Pull one JSON object out of a model reply.

    Small models wrap JSON in prose or a markdown fence far more often than big
    ones, so try a strict read, then a fence, then the first balanced {...}.
    """
    text = raw.strip()
    candidates = [text]
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value

    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
        start = text.find("{", start + 1)

    snippet = text[:120].replace("\n", " ")
    raise InvalidAction(f"your reply was not a JSON object: {snippet!r}")


def usage_snapshot(model: object) -> dict:
    """Token counters, if the model keeps them (the scripted test models don't)."""
    return dict(getattr(model, "usage", {}) or {})
