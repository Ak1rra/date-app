"""The AI client (AI-1..4): one `run` call that does a whole tool-using, structured-output turn.

`AnthropicAIClient` talks to Claude through the Anthropic SDK; `FakeAIClient` is the
deterministic stand-in for DEMO_MODE and tests. Callers depend on the `AIClient` protocol and
get one from `get_ai_client` / the `get_ai` FastAPI dependency.

The final answer is always a structured output validated by the caller's Pydantic model; free
text is never parsed. Any failure surfaces as `AIError`, which carries the usage spent so far so
the caller can still record it (`caps.finalize`).
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Literal, Protocol, cast

import anthropic
from anthropic import AsyncAnthropic, transform_schema
from fastapi import Depends
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.deps import get_app_settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

Tier = Literal["fast", "smart"]
ErrorCode = Literal["ai_unavailable", "refused", "truncated", "invalid_output"]

# Beta that lets the API re-run a safety-classifier refusal on another model (smart tier only:
# Haiku 5.5 has no server-side fallback).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_OUTPUT_TOKENS = 16_000  # non-streaming ceiling that stays under the SDK's HTTP timeout
REQUEST_TIMEOUT_S = 90.0


@dataclass(frozen=True)
class Usage:
    model: str
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    # What to price, one (model, input, output, cache_read, cache_write) per attempt: server-side
    # fallbacks bill at the fallback model's rates. Empty means "the totals above, at `model`".
    # Left out of equality: it is pricing detail, and the totals say what was used.
    lines: tuple[tuple[str, int, int, int, int], ...] = field(default=(), compare=False)

    def priced_lines(self) -> tuple[tuple[str, int, int, int, int], ...]:
        """The (model, input, output, cache_read, cache_write) lines to price: `lines`, else one
        line from the totals (none at all when nothing was used)."""
        if self.lines:
            return self.lines
        counts = (self.input, self.output, self.cache_read, self.cache_write)
        return ((self.model, *counts),) if any(counts) else ()

    def __add__(self, other: "Usage") -> "Usage":
        """Sum of the counts; the model is the later one's (the one that answered last). Every
        priced line is kept."""
        return Usage(
            model=other.model or self.model,
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
            lines=self.priced_lines() + other.priced_lines(),
        )

    @property
    def total_tokens(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write


@dataclass
class AIResult[T: BaseModel]:
    output: T
    usage: Usage
    tool_calls: int = 0
    degraded: set[str] = field(default_factory=set)


class AIError(Exception):
    """A run that produced no usable output. `code` is one of:
    ai_unavailable (SDK/network/API error), refused (safety refusal), truncated (hit max_tokens
    or the tool-iteration cap) or invalid_output (the structured output failed validation)."""

    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        *,
        usage: Usage | None = None,
        tool_calls: int = 0,
    ) -> None:
        super().__init__(message or code)
        self.code: ErrorCode = code
        self.usage = usage
        self.tool_calls = tool_calls


class ToolRunner(Protocol):
    """What the client needs from the tool executor (implemented by `app.ai.tools`)."""

    @property
    def degraded(self) -> set[str]:
        """Providers that were unavailable during this run."""
        ...

    def specs(self) -> list[dict[str, Any]]:
        """Strict JSON-schema tool definitions, in a stable order (the tool list is cached)."""
        ...

    async def execute(self, name: str, input: dict[str, Any]) -> str:
        """Run one tool call and return its result as a JSON string. Must not raise for
        provider failures: report them in the result and in `degraded`."""
        ...


@dataclass(frozen=True)
class TextDelta:
    """A piece of the reply text, as it streams."""

    text: str


@dataclass(frozen=True)
class ToolStarted:
    name: str


@dataclass(frozen=True)
class ToolFinished:
    name: str


@dataclass(frozen=True)
class ChatFinished:
    """The last event of a chat stream. `text` is everything streamed, across all tool rounds."""

    text: str
    usage: Usage
    tool_calls: int
    degraded: set[str]


ChatEvent = TextDelta | ToolStarted | ToolFinished | ChatFinished


class AIClient(Protocol):
    async def run[T: BaseModel](
        self,
        *,
        tier: Tier,
        system: str,
        context: str,
        messages: list[dict[str, Any]],
        tools: ToolRunner | None,
        output_model: type[T],
    ) -> AIResult[T]: ...

    def chat_stream(
        self,
        *,
        system: str,
        context: str,
        messages: list[dict[str, Any]],
        tools: ToolRunner | None,
    ) -> AsyncIterator[ChatEvent]:
        """A free-text, streaming turn with the same tool loop as `run`, always on the smart
        tier. Yields deltas and tool events, then one `ChatFinished`. Failures raise `AIError`,
        possibly after some deltas: the caller decides what to do with the partial text."""
        ...


_CALLER_CACHE_CONTROL = "callers must not set cache_control; the client places cache markers"


def _usage_of(response: Any) -> Usage:
    """Tokens for the whole response. With server-side fallbacks `usage.iterations` lists every
    attempt (the top-level counts cover only the one that answered), so it is summed instead."""
    u = response.usage
    iterations = getattr(u, "iterations", None)
    if not iterations:
        return Usage(
            model=str(response.model),
            input=u.input_tokens or 0,
            output=u.output_tokens or 0,
            cache_read=u.cache_read_input_tokens or 0,
            cache_write=u.cache_creation_input_tokens or 0,
        )
    lines = tuple(
        (
            str(getattr(i, "model", None) or response.model),
            getattr(i, "input_tokens", 0) or 0,
            getattr(i, "output_tokens", 0) or 0,
            getattr(i, "cache_read_input_tokens", 0) or 0,
            getattr(i, "cache_creation_input_tokens", 0) or 0,
        )
        for i in iterations
    )
    return Usage(
        model=str(response.model),
        input=sum(line[1] for line in lines),
        output=sum(line[2] for line in lines),
        cache_read=sum(line[3] for line in lines),
        cache_write=sum(line[4] for line in lines),
        lines=lines,
    )


def _with_cache_marker(convo: list[Any]) -> list[Any]:
    """A copy of `convo` whose last content block carries a cache breakpoint, so the next round
    of the tool loop reads everything before it from the cache. Copies only what it changes: the
    stored history never gets a marker, so markers can't pile up past the API's limit of 4."""
    if not convo:
        return convo
    last = convo[-1]
    content = last["content"]
    if isinstance(content, str):
        blocks: list[Any] = [{"type": "text", "text": content}]
    else:
        blocks = list(content)
    if not blocks or not isinstance(blocks[-1], dict):
        return convo  # SDK block objects can't take the marker; skip it rather than guess
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return [*convo[:-1], {**last, "content": blocks}]


@lru_cache
def _sdk_client(api_key: str) -> AsyncAnthropic:
    """One SDK client (one connection pool) per key, shared by every request."""
    return AsyncAnthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=2)


def _reject_caller_cache_control(
    messages: list[dict[str, Any]], specs: list[dict[str, Any]]
) -> None:
    """The client places every cache marker itself (the API allows 4), so a caller's own would
    add to them unseen. Shallow: message blocks and tool specs."""

    def marked(item: Any) -> bool:
        return isinstance(item, dict) and "cache_control" in item

    for message in messages:
        content = message.get("content")
        if marked(message) or (isinstance(content, list) and any(marked(b) for b in content)):
            raise ValueError(_CALLER_CACHE_CONTROL)
    if any(marked(spec) for spec in specs):
        raise ValueError(_CALLER_CACHE_CONTROL)


class AnthropicAIClient:
    def __init__(self, settings: Settings, sdk: AsyncAnthropic | None = None) -> None:
        if sdk is None:
            if not settings.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY is not set")
            sdk = _sdk_client(settings.anthropic_api_key)
        self._settings = settings
        self._sdk = sdk

    def _request(
        self, tier: Tier, system: str, context: str, output_model: type[BaseModel] | None
    ) -> dict[str, Any]:
        """The parts of the request that stay the same for every round of the tool loop. With no
        `output_model` the reply is free text (no `format`)."""
        s = self._settings
        smart = tier == "smart"
        request: dict[str, Any] = {
            "model": s.ai_model_smart if smart else s.ai_model_fast,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": s.ai_effort_smart if smart else s.ai_effort_fast},
            # Caching: the request renders tools, then the stable system prompt, then the couple's
            # context (its own block, unmarked, so it can change without touching what comes
            # before it), then the messages. One breakpoint ends the stable system prompt, so
            # tools plus it are cached whatever the context; `run` adds another on the last
            # message block of each round so the tool loop reads its earlier rounds.
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": context},
            ],
        }
        if output_model is not None:
            request["output_config"]["format"] = {
                "type": "json_schema",
                "schema": transform_schema(output_model),
            }
        if smart:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"
        return request

    async def run[T: BaseModel](
        self,
        *,
        tier: Tier,
        system: str,
        context: str,
        messages: list[dict[str, Any]],
        tools: ToolRunner | None,
        output_model: type[T],
    ) -> AIResult[T]:
        specs = tools.specs() if tools is not None else []
        _reject_caller_cache_control(messages, specs)
        request = self._request(tier, system, context, output_model)
        if specs:
            request["tools"] = specs
        convo: list[Any] = list(messages)
        max_rounds = self._settings.ai_max_tool_iterations
        usage = Usage(model=request["model"])
        tool_calls = 0

        def fail(code: ErrorCode, message: str) -> AIError:
            return AIError(code, message, usage=usage, tool_calls=tool_calls)

        # Up to `max_rounds` rounds of tool calls, then one request that must be the answer.
        for round_no in range(max_rounds + 1):
            try:
                response = await self._sdk.beta.messages.create(
                    **request, messages=_with_cache_marker(convo)
                )
            except anthropic.APIError as exc:
                logger.warning("anthropic request failed: %s: %s", type(exc).__name__, exc)
                raise fail("ai_unavailable", "The AI service is unavailable.") from exc
            usage = usage + _usage_of(response)
            stop = response.stop_reason

            if stop == "refusal":
                raise fail("refused", "The model declined this request.")
            if stop in ("max_tokens", "model_context_window_exceeded"):
                raise fail("truncated", f"The response was cut off ({stop}).")

            if stop in ("tool_use", "pause_turn"):
                if round_no == max_rounds:
                    raise fail("truncated", "The tool-use iteration limit was reached.")
                convo.append({"role": "assistant", "content": response.content})
                results: list[dict[str, Any]] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    if tools is None:
                        raise fail("ai_unavailable", "The model called a tool but none exist.")
                    tool_calls += 1
                    result = await self._call_tool(tools, block.name, block.input, block.id)
                    results.append(result)
                if results:
                    convo.append({"role": "user", "content": results})
                continue

            if stop not in ("end_turn", "stop_sequence"):
                raise fail("ai_unavailable", f"Unexpected stop reason {stop!r}.")
            text = "".join(b.text for b in response.content if b.type == "text")
            try:
                output = output_model.model_validate_json(text)
            except ValidationError as exc:
                raise fail("invalid_output", "The structured output did not validate.") from exc
            return AIResult(
                output=output,
                usage=usage,
                tool_calls=tool_calls,
                degraded=set(tools.degraded) if tools is not None else set(),
            )
        raise AssertionError("unreachable")  # the last round returns or raises

    async def chat_stream(
        self,
        *,
        system: str,
        context: str,
        messages: list[dict[str, Any]],
        tools: ToolRunner | None,
    ) -> AsyncIterator[ChatEvent]:
        specs = tools.specs() if tools is not None else []
        _reject_caller_cache_control(messages, specs)
        request = self._request("smart", system, context, None)
        if specs:
            request["tools"] = specs
        convo: list[Any] = list(messages)
        max_rounds = self._settings.ai_max_tool_iterations
        usage = Usage(model=request["model"])
        tool_calls = 0
        texts: list[str] = []

        def fail(code: ErrorCode, message: str) -> AIError:
            return AIError(code, message, usage=usage, tool_calls=tool_calls)

        for round_no in range(max_rounds + 1):
            try:
                async with self._sdk.beta.messages.stream(
                    **request, messages=_with_cache_marker(convo)
                ) as stream:
                    async for event in stream:
                        if event.type == "content_block_delta" and event.delta.type == "text_delta":
                            yield TextDelta(event.delta.text)
                    response = await stream.get_final_message()
            except Exception as exc:  # a dropped stream can also raise raw httpx errors
                logger.warning("anthropic stream failed: %s: %s", type(exc).__name__, exc)
                raise fail("ai_unavailable", "The AI service is unavailable.") from exc
            usage = usage + _usage_of(response)
            texts.append("".join(b.text for b in response.content if b.type == "text"))
            stop = response.stop_reason

            if stop == "refusal":
                raise fail("refused", "The model declined this request.")
            if stop in ("max_tokens", "model_context_window_exceeded"):
                raise fail("truncated", f"The response was cut off ({stop}).")

            if stop in ("tool_use", "pause_turn"):
                if round_no == max_rounds:
                    raise fail("truncated", "The tool-use iteration limit was reached.")
                convo.append({"role": "assistant", "content": response.content})
                results: list[dict[str, Any]] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    if tools is None:
                        raise fail("ai_unavailable", "The model called a tool but none exist.")
                    tool_calls += 1
                    yield ToolStarted(block.name)
                    result = await self._call_tool(tools, block.name, block.input, block.id)
                    yield ToolFinished(block.name)
                    results.append(result)
                if results:
                    convo.append({"role": "user", "content": results})
                continue

            if stop not in ("end_turn", "stop_sequence"):
                raise fail("ai_unavailable", f"Unexpected stop reason {stop!r}.")
            yield ChatFinished(
                text="".join(texts),
                usage=usage,
                tool_calls=tool_calls,
                degraded=set(tools.degraded) if tools is not None else set(),
            )
            return
        raise AssertionError("unreachable")  # the last round returns or raises

    @staticmethod
    async def _call_tool(
        tools: ToolRunner, name: str, tool_input: object, tool_use_id: str
    ) -> dict[str, Any]:
        try:
            content = await tools.execute(name, cast(dict[str, Any], tool_input))
            return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        except Exception:
            # A broken tool must not end the run: tell the model, which can carry on without.
            logger.exception("tool %s failed", name)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps({"error": f"{name} failed"}),
                "is_error": True,
            }


# --- The fake ---------------------------------------------------------------------------------

Responder = Callable[[ToolRunner | None, str, list[dict[str, Any]]], Awaitable[BaseModel]]

# The responders DEMO_MODE uses, keyed by output model. Feature modules register theirs here
# (`FAKE_RESPONDERS[IdeaBatch] = fake_ideas`); `get_ai_client` hands this dict to the fake.
FAKE_RESPONDERS: dict[type[BaseModel], Responder] = {}

ChatResponder = Callable[[ToolRunner | None, str, list[dict[str, Any]]], AsyncIterator[ChatEvent]]

# The chat responder DEMO_MODE uses; None means the fake's built-in canned reply.
FAKE_CHAT_RESPONDER: ChatResponder | None = None


class _CountingRunner:
    """Wraps a ToolRunner to count the calls a responder makes."""

    def __init__(self, inner: ToolRunner) -> None:
        self._inner = inner
        self.calls = 0

    @property
    def degraded(self) -> set[str]:
        return self._inner.degraded

    def specs(self) -> list[dict[str, Any]]:
        return self._inner.specs()

    async def execute(self, name: str, input: dict[str, Any]) -> str:
        self.calls += 1
        return await self._inner.execute(name, input)


class FakeAIClient:
    """Deterministic, no network. `responders` maps an output model to a coroutine
    `(tools, context, messages) -> output` that calls the tools itself and builds the output
    from what they return. Usage is zeros, so fake runs cost nothing."""

    def __init__(
        self,
        responders: dict[type[BaseModel], Responder] | None = None,
        chat_responder: ChatResponder | None = None,
    ) -> None:
        # Not `or {}`: an empty dict passed in is shared on purpose, so callers can extend it.
        self.responders = responders if responders is not None else {}
        self.chat_responder = chat_responder

    async def run[T: BaseModel](
        self,
        *,
        tier: Tier,
        system: str,
        context: str,
        messages: list[dict[str, Any]],
        tools: ToolRunner | None,
        output_model: type[T],
    ) -> AIResult[T]:
        responder = self.responders.get(output_model)
        if responder is None:
            raise AIError("ai_unavailable", f"No fake responder for {output_model.__name__}.")
        counter = _CountingRunner(tools) if tools is not None else None
        try:
            output = await responder(counter, context, messages)
        except AIError:
            raise
        except Exception as exc:
            logger.exception("fake responder for %s failed", output_model.__name__)
            raise AIError(
                "ai_unavailable",
                "The fake responder failed.",
                tool_calls=counter.calls if counter is not None else 0,
            ) from exc
        if not isinstance(output, output_model):
            raise AIError("invalid_output", f"The fake returned {type(output).__name__}.")
        return AIResult(
            output=output,
            usage=Usage(model="fake"),
            tool_calls=counter.calls if counter is not None else 0,
            degraded=set(tools.degraded) if tools is not None else set(),
        )

    async def chat_stream(
        self,
        *,
        system: str,
        context: str,
        messages: list[dict[str, Any]],
        tools: ToolRunner | None,
    ) -> AsyncIterator[ChatEvent]:
        """Delegates to `chat_responder` when set, else streams a canned reply (proposing an
        idea first when the user's message asks for one and the tool exists). Usage is zeros."""
        counter = _CountingRunner(tools) if tools is not None else None
        responder = self.chat_responder or _default_chat_responder
        try:
            async for event in responder(counter, context, messages):
                if isinstance(event, ChatFinished):
                    event = replace(event, tool_calls=counter.calls if counter else 0)
                yield event
        except AIError:
            raise
        except Exception as exc:
            logger.exception("fake chat responder failed")
            raise AIError(
                "ai_unavailable",
                "The fake responder failed.",
                tool_calls=counter.calls if counter is not None else 0,
            ) from exc


def _last_user_message_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
    return ""


_FAKE_IDEA: dict[str, Any] = {
    "title": "Sunset picnic",
    "pitch": "Pack a blanket and some snacks and watch the sun go down together.",
    "category": "outdoors",
    "est_cost": 25,
    "duration_minutes": 120,
    "best_time": "evening",
    "indoor_outdoor": "outdoor",
    "tags": ["cozy"],
}


async def _default_chat_responder(
    tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
) -> AsyncIterator[ChatEvent]:
    """The fake's reply when no `chat_responder` is set."""
    asks_for_idea = "idea" in _last_user_message_text(messages).lower()
    if (
        asks_for_idea
        and tools is not None
        and any(spec.get("name") == "propose_idea" for spec in tools.specs())
    ):
        yield ToolStarted("propose_idea")
        await tools.execute("propose_idea", dict(_FAKE_IDEA))
        yield ToolFinished("propose_idea")
    chunks = ["Here's a thought: ", "how about a ", "sunset picnic ", "this weekend?"]
    for chunk in chunks:
        yield TextDelta(chunk)
    yield ChatFinished(
        text="".join(chunks),
        usage=Usage(model="fake"),
        tool_calls=0,
        degraded=set(tools.degraded) if tools is not None else set(),
    )


# --- Selection --------------------------------------------------------------------------------


def get_ai_client(settings: Settings) -> AIClient | None:
    """Anthropic when the key is set (with or without DEMO_MODE), the fake in DEMO_MODE,
    otherwise None: AI is unavailable."""
    if settings.anthropic_api_key:
        return AnthropicAIClient(settings)
    if settings.demo_mode:
        import app.ai.fakes  # noqa: F401  (importing registers the demo responders)

        return FakeAIClient(FAKE_RESPONDERS, FAKE_CHAT_RESPONDER)
    return None


def get_ai(settings: Settings = Depends(get_app_settings)) -> AIClient:
    """FastAPI dependency; tests swap it with `app.dependency_overrides`."""
    client = get_ai_client(settings)
    if client is None:
        raise ApiError(503, "ai_unavailable", "The planner isn't available right now.")
    return client
