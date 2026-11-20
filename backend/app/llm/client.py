"""Model-agnostic LLM layer.

Everything else in the app talks to `LLMClient`; nothing imports a vendor SDK. Groq exposes an
OpenAI-compatible chat-completions API, so swapping to another provider/model is a matter of
changing LLM_BASE_URL / GROQ_MODEL / GROQ_API_KEY, or passing a different `LLMClient` subclass.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import get_settings

log = logging.getLogger("nyaya.llm")
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base error. `user_message` is safe to show in the UI."""

    user_message = "The AI service is temporarily unavailable. Please try again."


class LLMNotConfigured(LLMError):
    user_message = "GROQ_API_KEY is not set. Add it to the .env file and restart the backend."


class LLMInvalidResponse(LLMError):
    user_message = "The AI returned a response that could not be validated. Please try again."


class LLMClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None,
                 timeout: float | None = None, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.api_key = s.groq_api_key if api_key is None else api_key
        self.model = model or s.model_name
        self.base_url = (base_url or s.llm_base_url).rstrip("/")
        self.timeout = timeout or s.llm_timeout_seconds
        self._transport = transport  # injectable for tests
        self.max_rate_limit_retries = s.llm_max_retries if transport is None else 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def complete(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.2,
                 max_tokens: int = 2048) -> str:
        if not self.configured:
            raise LLMNotConfigured("GROQ_API_KEY missing")
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = None
        for attempt in range(self.max_rate_limit_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as http:
                    r = http.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
            except httpx.HTTPError as e:
                log.warning("LLM transport error: %s", e)
                raise LLMError(f"transport error: {e}") from e
            if r.status_code != 429 or attempt == self.max_rate_limit_retries:
                break
            try:
                wait = float(r.headers.get("retry-after", "5"))
            except ValueError:
                wait = 5.0
            # Per-minute limits clear in seconds -> wait and retry. A long Retry-After / x-should-retry:false
            # means a daily quota: retrying now is pointless, so fail fast with a clear message.
            if wait > 60 or r.headers.get("x-should-retry", "").lower() == "false":
                break
            wait = max(wait, 1.0)
            log.info("LLM rate-limited (429); retrying in %.1fs (attempt %d)", wait, attempt + 1)
            time.sleep(wait)
        if r.status_code == 401:
            err = LLMNotConfigured("invalid API key")
            err.user_message = "The GROQ_API_KEY was rejected (401). Check the key in .env."
            raise err
        if r.status_code == 400 and "json_validate_failed" in r.text:
            # Provider-side JSON-mode validation failed (model wrote prose). Same as invalid output: retryable.
            log.warning("LLM JSON mode validation failed: %s", r.text[:300])
            raise LLMInvalidResponse("json_validate_failed")
        if r.status_code >= 400:
            log.warning("LLM HTTP %s: %s", r.status_code, r.text[:500])
            err = LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            if r.status_code == 429:
                try:
                    mins = max(1, round(float(r.headers.get("retry-after", "60")) / 60))
                except ValueError:
                    mins = 1
                daily = "per day" in r.text or "TPD" in r.text or "RPD" in r.text
                err.user_message = (f"The AI provider's {'daily quota' if daily else 'rate limit'} was reached. "
                                    f"Please retry in about {mins} minute{'s' if mins > 1 else ''}"
                                    + (", or set a different GROQ_MODEL in .env." if daily else "."))
            raise err
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as e:
            raise LLMInvalidResponse(f"unexpected response shape: {r.text[:300]}") from e

    def complete_json(self, messages: list[dict], schema: type[T], *, temperature: float = 0.1,
                      max_tokens: int = 2048, retries: int = 1) -> T:
        """Ask for JSON, parse it and validate with Pydantic. On failure, ask the model to repair once."""
        convo = list(messages)
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                raw = self.complete(convo, json_mode=True, temperature=temperature, max_tokens=max_tokens)
            except LLMInvalidResponse as e:
                last_err = e
                convo = list(messages) + [{"role": "user", "content": (
                    "Your previous answer was not valid JSON. Respond again with ONLY the JSON object.")}]
                continue
            try:
                return schema.model_validate(parse_json_loose(raw))
            except (ValueError, ValidationError) as e:
                last_err = e
                log.warning("Invalid structured output (attempt %d): %s", attempt + 1, str(e)[:300])
                convo = list(messages) + [
                    {"role": "assistant", "content": raw[:4000]},
                    {"role": "user", "content": (
                        "Your previous output was not valid for the required JSON schema. Error: "
                        f"{str(e)[:600]}\nReturn ONLY a corrected JSON object.")},
                ]
        raise LLMInvalidResponse(str(last_err))


def parse_json_loose(raw: str) -> dict:
    """Parse a JSON object, tolerating markdown fences or prose around it."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output")
        obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model output JSON is not an object")
    return obj


_default: LLMClient | None = None


def get_llm() -> LLMClient:
    global _default
    if _default is None:
        _default = LLMClient()
    return _default


def set_llm(client: LLMClient | None) -> None:
    """Swap the process-wide client (used by tests, or to plug in a different model)."""
    global _default
    _default = client
