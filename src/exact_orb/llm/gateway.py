"""Transport-only LiteLLM gateway for exact-orb."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
import re
import time
from typing import Any

from pydantic import BaseModel

from exact_orb.config import read_exact_orb_pyproject_value


LOGGER = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_LLM_TIMEOUT = 60.0
DEFAULT_LLM_RETRIES = 2
LLM_MODEL_ENV_VAR = "EXACT_ORB_LLM_MODEL"
LLM_TIMEOUT_ENV_VAR = "EXACT_ORB_LLM_TIMEOUT"
LLM_RETRIES_ENV_VAR = "EXACT_ORB_LLM_RETRIES"
_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_API_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token|secret|password)(\s*[=:]\s*)([^,\s)]+)"
)
_LITELLM: Any | None = None


class LLMGatewayError(RuntimeError):
    """Raised when the configured LLM provider cannot complete a request."""


class LLMResponse(BaseModel):
    """Normalized response returned by the transport gateway."""

    text: str
    model: str
    provider: str
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    latency_ms: float


def complete(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    retries: int | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """Complete one prompt through LiteLLM.

    LiteLLM 1.97.0 documents ``max_retries`` as the number of retry
    attempts after the first call; this wrapper passes the resolved
    ``retries`` value to that parameter and does not implement its own loop.
    """

    _reject_ambiguous_kwargs(kwargs)
    resolved_model = _resolve_string_setting(
        model,
        LLM_MODEL_ENV_VAR,
        "llm_model",
        DEFAULT_LLM_MODEL,
    )
    resolved_timeout = _resolve_timeout(timeout)
    resolved_retries = _resolve_retries(retries)
    litellm = _litellm_module()
    provider = _resolve_provider(litellm, resolved_model)
    messages = _build_messages(prompt, system)
    prompt_chars = len(prompt) + (len(system) if system is not None else 0)

    LOGGER.debug("llm_prompt system=%r user=%r", system, prompt)
    started = time.perf_counter()
    try:
        response = litellm.completion(
            model=resolved_model,
            messages=messages,
            timeout=resolved_timeout,
            max_retries=resolved_retries,
            **kwargs,
        )
    except Exception as exc:
        latency_ms = _elapsed_ms(started)
        sanitized_error = _redact_secrets(str(exc))
        sanitized_exc = LLMGatewayError(sanitized_error)
        LOGGER.info(
            "llm_call status=error provider=%s model=%s prompt_chars=%d "
            "tokens_in=%s tokens_out=%s cost_usd=%s latency_ms=%.3f",
            provider,
            resolved_model,
            prompt_chars,
            None,
            None,
            None,
            latency_ms,
        )
        LOGGER.error(
            "llm_call_failed provider=%s model=%s error=%s",
            provider,
            resolved_model,
            sanitized_error,
            exc_info=(LLMGatewayError, sanitized_exc, exc.__traceback__),
        )
        raise LLMGatewayError(
            "LLM provider error for provider=%s model=%s: %s"
            % (provider, resolved_model, sanitized_error)
        ) from None

    latency_ms = _elapsed_ms(started)
    text = _extract_text(response)
    tokens_in = _usage_int(response, "prompt_tokens")
    tokens_out = _usage_int(response, "completion_tokens")
    cost_usd = _completion_cost(litellm, response, resolved_model)
    response_model = _response_model(response, resolved_model)

    LOGGER.info(
        "llm_call status=ok provider=%s model=%s prompt_chars=%d "
        "tokens_in=%s tokens_out=%s cost_usd=%s latency_ms=%.3f",
        provider,
        response_model,
        prompt_chars,
        tokens_in,
        tokens_out,
        cost_usd,
        latency_ms,
    )
    LOGGER.debug("llm_response text=%r", text)
    return LLMResponse(
        text=text,
        model=response_model,
        provider=provider,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def _reject_ambiguous_kwargs(kwargs: Mapping[str, Any]) -> None:
    duplicates = {"max_retries", "num_retries", "request_timeout"} & set(kwargs)
    if duplicates:
        names = ", ".join(sorted(duplicates))
        raise ValueError(
            "Use exact_orb.llm.complete timeout/retries parameters instead of "
            "LiteLLM transport kwargs: %s" % names
        )
    if "api_key" in kwargs:
        raise ValueError("API keys must come from provider environment variables")
    if kwargs.get("stream") is True:
        raise ValueError("Streaming completions are not supported by exact_orb.llm.complete")


def _build_messages(prompt: str, system: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _resolve_string_setting(
    argument: str | None,
    env_name: str,
    pyproject_name: str,
    default: str,
) -> str:
    value = _first_configured(
        argument,
        os.environ.get(env_name),
        read_exact_orb_pyproject_value(pyproject_name),
        default,
    )
    return str(value)


def _resolve_timeout(argument: float | None) -> float:
    value = _first_configured(
        argument,
        os.environ.get(LLM_TIMEOUT_ENV_VAR),
        read_exact_orb_pyproject_value("llm_timeout"),
        DEFAULT_LLM_TIMEOUT,
    )
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("llm_timeout must be a positive number of seconds") from exc
    if parsed <= 0:
        raise ValueError("llm_timeout must be a positive number of seconds")
    return parsed


def _resolve_retries(argument: int | None) -> int:
    value = _first_configured(
        argument,
        os.environ.get(LLM_RETRIES_ENV_VAR),
        read_exact_orb_pyproject_value("llm_retries"),
        DEFAULT_LLM_RETRIES,
    )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("llm_retries must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError("llm_retries must be a non-negative integer")
    return parsed


def _first_configured(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _litellm_module() -> Any:
    global _LITELLM
    if _LITELLM is None:
        import litellm

        _LITELLM = litellm
    return _LITELLM


def _resolve_provider(litellm: Any, model: str) -> str:
    try:
        _model, provider, _dynamic_api_key, _api_base = litellm.get_llm_provider(model)
    except Exception:
        return model.split("/", 1)[0] if "/" in model else model
    return provider or (model.split("/", 1)[0] if "/" in model else model)


def _extract_text(response: Any) -> str:
    choices = _get_value(response, "choices")
    if not choices:
        return ""
    first_choice = choices[0]
    message = _get_value(first_choice, "message")
    content = _get_value(message, "content")
    if content is None:
        content = _get_value(first_choice, "text")
    return "" if content is None else str(content)


def _usage_int(response: Any, name: str) -> int | None:
    usage = _get_value(response, "usage")
    value = _get_value(usage, name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _completion_cost(litellm: Any, response: Any, model: str) -> float | None:
    try:
        return float(litellm.completion_cost(completion_response=response, model=model))
    except Exception:
        return None


def _response_model(response: Any, fallback: str) -> str:
    value = _get_value(response, "model")
    return fallback if value in (None, "") else str(value)


def _get_value(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _redact_secrets(text: str) -> str:
    redacted = text
    for name, value in os.environ.items():
        if len(value) < 8:
            continue
        if any(part in name.upper() for part in _SECRET_NAME_PARTS):
            redacted = redacted.replace(value, "[REDACTED]")
    return _API_SECRET_RE.sub(r"\1\2[REDACTED]", redacted)


__all__ = [
    "LLMGatewayError",
    "LLMResponse",
    "complete",
]
