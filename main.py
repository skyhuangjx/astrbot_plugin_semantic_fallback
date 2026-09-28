"""Convert primary-model refusal text into AstraBot's native fallback signal."""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncGenerator
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.provider.entities import LLMResponse


ORIGINAL_ATTR = "_semantic_fallback_original_iter_llm_responses"
WRAPPER_ATTR = "_semantic_fallback_wrapper"
DEFAULT_REFUSAL_PATTERNS = [
    r"无法(?:参与|继续|提供|协助|完成)",
    r"不能(?:参与|继续|提供|协助|完成)",
    r"不(?:能|可以)进行.*(?:角色扮演|虚拟情境|此类内容)",
    r"(?:我是|作为)(?:一个)?(?:人工智能|大语言|AI).{0,30}(?:模型|助手)",
    r"如果您有其他.*(?:话题|问题).{0,20}(?:可以|能够)",
]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _response_text(response: LLMResponse) -> str:
    try:
        return _normalize_text(response.completion_text)
    except Exception:
        return ""


@register(
    "astrbot_plugin_semantic_fallback",
    "Codex",
    "Treat configured primary-model refusal text as an LLM error.",
    "0.2.0",
)
class SemanticFallbackPlugin(Star):
    """Runtime adapter for semantic failures returned as normal text."""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._wrapper = None
        self._original = None
        self._patterns = self._compile_patterns()

    async def initialize(self) -> None:
        if not self._as_bool("enabled", True):
            logger.info("[semantic_fallback] disabled by configuration")
            return

        runner_cls = ToolLoopAgentRunner
        current = getattr(runner_cls, "_iter_llm_responses", None)
        if current is None:
            logger.error(
                "[semantic_fallback] incompatible AstraBot runner: "
                "_iter_llm_responses is unavailable; plugin disabled",
            )
            return
        if getattr(runner_cls, WRAPPER_ATTR, False):
            logger.warning("[semantic_fallback] runner is already patched; skipping")
            return
        if not inspect.isasyncgenfunction(current):
            logger.error(
                "[semantic_fallback] incompatible AstraBot runner: "
                "_iter_llm_responses is not an async generator; plugin disabled",
            )
            return

        self._original = current
        plugin = self

        async def wrapped(
            runner: Any,
            *args: Any,
            **kwargs: Any,
        ) -> AsyncGenerator[LLMResponse, None]:
            if not plugin._is_primary_runner(runner):
                async for response in current(runner, *args, **kwargs):
                    yield response
                return

            if not getattr(runner, "streaming", False):
                async for response in current(runner, *args, **kwargs):
                    if plugin._is_refusal(response):
                        plugin._mark_error(runner, response)
                    yield response
                return

            if not plugin._as_bool("buffer_streaming_primary", True):
                async for response in current(runner, *args, **kwargs):
                    yield response
                return

            buffered: list[LLMResponse] = []
            async for response in current(runner, *args, **kwargs):
                buffered.append(response)
            final = next(
                (response for response in reversed(buffered) if not response.is_chunk),
                None,
            )
            combined = "".join(
                _response_text(response)
                for response in buffered
                if response.is_chunk
            )
            if final is not None and plugin._is_refusal(final, extra_text=combined):
                plugin._mark_error(runner, final)
                yield final
                return
            if final is None and plugin._is_refusal_text(combined):
                yield LLMResponse(
                    role="err",
                    completion_text="Primary provider returned a semantic refusal.",
                )
                return
            for response in buffered:
                yield response

        setattr(runner_cls, ORIGINAL_ATTR, current)
        setattr(runner_cls, WRAPPER_ATTR, True)
        runner_cls._iter_llm_responses = wrapped
        self._wrapper = wrapped
        logger.info(
            "[semantic_fallback] enabled; primary/fallback providers follow AstraBot",
        )

    async def terminate(self) -> None:
        runner_cls = ToolLoopAgentRunner
        if self._wrapper is not None and getattr(
            runner_cls, "_iter_llm_responses", None
        ) is self._wrapper:
            original = getattr(runner_cls, ORIGINAL_ATTR, self._original)
            if original is not None:
                runner_cls._iter_llm_responses = original
            for attr in (ORIGINAL_ATTR, WRAPPER_ATTR):
                try:
                    delattr(runner_cls, attr)
                except AttributeError:
                    pass
            logger.info("[semantic_fallback] runner patch removed")

    def _compile_patterns(self) -> list[re.Pattern[str]]:
        raw = self.config.get("refusal_patterns", DEFAULT_REFUSAL_PATTERNS)
        if not isinstance(raw, list):
            raw = DEFAULT_REFUSAL_PATTERNS
        patterns: list[re.Pattern[str]] = []
        for item in raw:
            try:
                patterns.append(re.compile(str(item), re.IGNORECASE | re.DOTALL))
            except re.error as exc:
                logger.warning(
                    "[semantic_fallback] invalid refusal pattern %r: %s",
                    item,
                    exc,
                )
        return patterns

    def _as_bool(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        return value if isinstance(value, bool) else default

    def _is_primary_runner(self, runner: Any) -> bool:
        provider = getattr(runner, "provider", None)
        fallback_providers = getattr(runner, "fallback_providers", ()) or ()
        return provider is not None and all(
            provider is not fallback for fallback in fallback_providers
        )

    def _is_refusal(self, response: LLMResponse, extra_text: str = "") -> bool:
        if response.role != "assistant" or response.tools_call_name:
            return False
        text = _response_text(response)
        if extra_text:
            text = _normalize_text(f"{text} {extra_text}")
        return self._is_refusal_text(text)

    def _is_refusal_text(self, text: str) -> bool:
        if not text or not self._patterns:
            return False
        match_count = sum(1 for pattern in self._patterns if pattern.search(text))
        minimum = self.config.get("minimum_matches", 2)
        try:
            minimum = max(1, int(minimum))
        except (TypeError, ValueError):
            minimum = 2
        return match_count >= minimum

    def _mark_error(self, runner: Any, response: LLMResponse) -> None:
        response.role = "err"
        if self._as_bool("log_matches", True):
            provider = getattr(runner, "provider", None)
            provider_id = getattr(provider, "provider_config", {}).get(
                "id", "<unknown>"
            )
            logger.warning(
                "[semantic_fallback] classified primary response as refusal; "
                "provider=%s; fallback loop will try the next provider",
                provider_id,
            )
