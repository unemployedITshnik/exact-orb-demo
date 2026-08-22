"""LLM gateway tests."""

from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterator
from unittest.mock import Mock

import pytest

from exact_orb.llm import LLMGatewayError, LLMResponse, complete
from exact_orb.llm import gateway


def test_complete_returns_normalized_response_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sk-test-secret-123456"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    monkeypatch.setenv("EXACT_ORB_LLM_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.setenv("EXACT_ORB_LLM_TIMEOUT", "12.5")
    monkeypatch.setenv("EXACT_ORB_LLM_RETRIES", "4")
    completion = Mock(
        return_value=_model_response(
            text="pong",
            model="deepseek/deepseek-v4-flash",
            prompt_tokens=3,
            completion_tokens=4,
        )
    )
    completion_cost = Mock(return_value=0.00000154)
    monkeypatch.setattr(gateway, "_LITELLM", _fake_litellm(completion, completion_cost))

    with _capture_gateway_logs(caplog):
        result = complete("ping", system="system text", temperature=0)

    assert result == LLMResponse(
        text="pong",
        model="deepseek/deepseek-v4-flash",
        provider="deepseek",
        tokens_in=3,
        tokens_out=4,
        cost_usd=0.00000154,
        latency_ms=result.latency_ms,
    )
    assert result.latency_ms >= 0

    call_kwargs = completion.call_args.kwargs
    assert call_kwargs["model"] == "deepseek/deepseek-v4-flash"
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "ping"},
    ]
    assert call_kwargs["timeout"] == 12.5
    assert call_kwargs["max_retries"] == 4
    assert call_kwargs["temperature"] == 0
    assert "api_key" not in call_kwargs
    completion_cost.assert_called_once()

    messages = _logged_messages(caplog)
    assert "llm_call status=ok provider=deepseek model=deepseek/deepseek-v4-flash" in messages
    assert "prompt_chars=15" in messages
    assert "tokens_in=3 tokens_out=4 cost_usd=1.54e-06" in messages
    assert "llm_prompt system='system text' user='ping'" in messages
    assert "llm_response text='pong'" in messages
    assert secret not in messages


def test_complete_wraps_litellm_errors_and_logs_traceback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sk-test-secret-abcdef"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    completion = Mock(side_effect=RuntimeError("provider said 429 api_key=%s" % secret))
    monkeypatch.setattr(gateway, "_LITELLM", _fake_litellm(completion, Mock()))

    with _capture_gateway_logs(caplog):
        with pytest.raises(LLMGatewayError) as exc_info:
            complete(
                "ping",
                model="deepseek/deepseek-v4-flash",
                timeout=3,
                retries=5,
            )

    assert completion.call_count == 1
    call_kwargs = completion.call_args.kwargs
    assert call_kwargs["timeout"] == 3
    assert call_kwargs["max_retries"] == 5
    assert "[REDACTED]" in str(exc_info.value)
    assert secret not in str(exc_info.value)

    messages = _logged_messages(caplog)
    assert "llm_call status=error provider=deepseek model=deepseek/deepseek-v4-flash" in messages
    assert "llm_call_failed provider=deepseek model=deepseek/deepseek-v4-flash" in messages
    assert secret not in messages
    assert any(record.levelno == logging.ERROR and record.exc_info for record in caplog.records)
    formatted_tracebacks = "\n".join(
        logging.Formatter().formatException(record.exc_info)
        for record in caplog.records
        if record.exc_info
    )
    assert "RuntimeError" not in formatted_tracebacks
    assert "[REDACTED]" in formatted_tracebacks
    assert secret not in formatted_tracebacks


def test_complete_handles_missing_usage_and_unknown_cost(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    completion = Mock(
        return_value=_model_response(
            text="plain text",
            model="custom/provider-model",
            choices=[
                {
                    "message": {"content": "plain text", "role": "assistant"},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            usage=None,
        )
    )
    completion_cost = Mock(side_effect=Exception("model not found in cost map"))
    monkeypatch.setattr(gateway, "_LITELLM", _fake_litellm(completion, completion_cost))

    with _capture_gateway_logs(caplog):
        result = complete(
            "hello",
            model="custom/provider-model",
            timeout=1,
            retries=0,
        )

    assert result.text == "plain text"
    assert result.provider == "custom"
    assert result.tokens_in is None
    assert result.tokens_out is None
    assert result.cost_usd is None
    assert "tokens_in=None tokens_out=None cost_usd=None" in _logged_messages(caplog)


def test_complete_rejects_ambiguous_transport_kwargs() -> None:
    with pytest.raises(ValueError, match="timeout/retries"):
        complete("ping", max_retries=3)
    with pytest.raises(ValueError, match="environment"):
        complete("ping", api_key="sk-direct-key")
    with pytest.raises(ValueError, match="Streaming"):
        complete("ping", stream=True)


def test_import_llm_does_not_import_upper_or_calculation_layers() -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    script = (
        "import json, sys; "
        "import exact_orb.llm; "
        "forbidden = ["
        "'exact_orb.engine', 'exact_orb.intent', 'exact_orb.tools', "
        "'exact_orb.interpretation', 'exact_orb.orchestration', "
        "'exact_orb.engine.ephemeris', 'exact_orb.engine.charts', 'exact_orb.engine.aspects', "
        "'exact_orb.engine.configurations', 'exact_orb.engine.strength']; "
        "loaded = [name for name in forbidden if name in sys.modules]; "
        "print(json.dumps(loaded)); "
        "raise SystemExit(1 if loaded else 0)"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "[]"


def _model_response(
    *,
    text: str,
    model: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    choices: list[dict[str, Any]] | None = None,
    usage: Any = "default",
) -> dict[str, Any]:
    if choices is None:
        choices = [
            {
                "message": {"content": text, "role": "assistant"},
                "finish_reason": "stop",
                "index": 0,
            }
        ]
    if usage == "default":
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
        }
    return {
        "id": "test",
        "choices": choices,
        "model": model,
        "usage": usage,
    }


def _fake_litellm(completion: Mock, completion_cost: Mock) -> object:
    class FakeLiteLLM:
        pass

    fake = FakeLiteLLM()
    fake.completion = completion
    fake.completion_cost = completion_cost
    fake.get_llm_provider = _fake_get_llm_provider
    return fake


def _fake_get_llm_provider(model: str) -> tuple[str, str, None, None]:
    if "/" not in model:
        raise RuntimeError("provider missing")
    provider, model_name = model.split("/", 1)
    if provider == "custom":
        raise RuntimeError("provider unknown")
    return model_name, provider, None, None


@contextmanager
def _capture_gateway_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    logger = logging.getLogger("exact_orb.llm.gateway")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    logger.handlers[:] = [caplog.handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    caplog.clear()
    try:
        yield
    finally:
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def _logged_messages(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)
