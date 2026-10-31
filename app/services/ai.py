"""Groq chat completions, returning JSON that matches a schema.

Only the CV text and the job post are sent: no names of other users, no ids, no keys. The free plan is
rate-limited per minute and per day (see https://console.groq.com/settings/limits), so callers keep
results instead of asking twice.
"""

import json
import logging

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Models that accept response_format json_schema with strict: true (constrained decoding).
_STRICT_PREFIXES = ("openai/gpt-oss-", "qwen/", "meta-models/")


class AiUnavailableError(Exception):
    """No key configured, the key was rejected, or Groq couldn't be reached."""


class AiRateLimitedError(Exception):
    def __init__(self, retry_after: int | None):
        super().__init__("rate limited")
        self.retry_after = retry_after


class AiTooLargeError(Exception):
    pass


class AiBadResponseError(Exception):
    pass


class GroqClient:
    def __init__(self, settings: Settings):
        self._key = settings.groq_api_key
        self.model = settings.groq_model

    @property
    def available(self) -> bool:
        return bool(self._key)

    def json(self, *, system: str, user: str, schema_name: str, schema: dict, max_tokens: int) -> dict:
        if not self._key:
            raise AiUnavailableError()
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_completion_tokens": max_tokens,
        }
        if self.model.startswith(_STRICT_PREFIXES):
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        else:
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] += "\n\nReply with JSON only, matching this schema:\n" + json.dumps(schema)
        if self.model.startswith("openai/gpt-oss-"):
            # Reasoning tokens count against the per-minute limit; this task doesn't need many.
            body["reasoning_effort"] = "low"

        try:
            response = httpx.post(
                GROQ_URL, json=body, headers={"Authorization": f"Bearer {self._key}"}, timeout=90.0
            )
        except httpx.HTTPError as exc:
            logger.warning("Groq request failed: %s", type(exc).__name__)
            raise AiUnavailableError() from exc

        if response.status_code == 429:
            raise AiRateLimitedError(_retry_after(response))
        if response.status_code == 413:
            raise AiTooLargeError()
        if response.status_code in (401, 403):
            logger.warning("Groq rejected the API key (%s)", response.status_code)
            raise AiUnavailableError()
        if response.status_code != 200:
            logger.warning("Groq returned %s: %s", response.status_code, _error_code(response))
            if response.status_code == 400 and "too large" in response.text.lower():
                raise AiTooLargeError()
            raise AiBadResponseError()

        try:
            content = response.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Groq reply wasn't valid JSON")
            raise AiBadResponseError() from exc
        if not isinstance(result, dict):
            raise AiBadResponseError()
        return result


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    try:
        return max(1, round(float(value))) if value else None
    except ValueError:
        return None


def _error_code(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        return str(error.get("code") or error.get("type") or "unknown")
    except ValueError:
        return "unknown"
