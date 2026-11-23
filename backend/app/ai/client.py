"""The AI client (AI-1..4): one `run` call that does a whole tool-using, structured-output turn.

`OpenAIClient` talks to OpenAI through the Responses API (official `openai` SDK); `FakeAIClient`
is the deterministic stand-in for DEMO_MODE and tests. Callers depend on the `AIClient` protocol
and get one from `get_ai_client` / the `get_ai` FastAPI dependency.

The final answer is always a structured output validated by the caller's Pydantic model; free
text is never parsed. Any failure surfaces as `AIError`, which carries the usage spent so far so
the caller can still record it (`caps.finalize`).
"""

import copy
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Literal, Protocol

import openai
from fastapi import Depends
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.deps import get_app_settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

Tier = Literal["fast", "smart"]
ErrorCode = Literal["ai_unavailable", "refused", "truncated", "invalid_output"]

MAX_OUTPUT_TOKENS = 16_000  # reasoning tokens count against it, so it is generous
REQUEST_TIMEOUT_S = 90.0


@dataclass(frozen=True)
class Usage:
    model: str
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    # What to price, one (model, input, output, cache_read, cache_write) per attempt. Empty means
    # "the totals above, at `model`" (always the case for OpenAI; only the cap code reads it).
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
    ai_unavailable (SDK/network/API error), refused (safety refusal), truncated (hit the output
    limit or the tool-iteration cap) or invalid_output (the structured output failed validation)."""

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


# --- Schemas ----------------------------------------------------------------------------------


def _is_nullable(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    kind = node.get("type")
    if kind == "null" or (isinstance(kind, list) and "null" in kind):
        return True
    return any(_is_nullable(branch) for branch in node.get("anyOf", ()))


def _note_limit(node: dict[str, Any], key: str, phrase: str) -> None:
    """Strict mode rejects minLength/maxLength: move the limit into the description, so the
    model still sees it (Pydantic enforces it when the output is validated)."""
    if key in node:
        note = f"({phrase} {node.pop(key)} characters)"
        node["description"] = f"{node['description']} {note}" if node.get("description") else note


def _strictify(node: Any, *, nullable_optional: bool) -> None:
    """Rewrite a schema in place for OpenAI strict mode: every object closed and fully required,
    no defaults or length limits. A property that was optional becomes required; with
    `nullable_optional` it also becomes nullable (tool arguments, where null means "not given"),
    without it the property's schema is left as is (a model's fields, which must still validate)."""
    if isinstance(node, list):
        for item in node:
            _strictify(item, nullable_optional=nullable_optional)
        return
    if not isinstance(node, dict):
        return
    node.pop("default", None)
    _note_limit(node, "minLength", "at least")
    _note_limit(node, "maxLength", "at most")
    for key in ("$defs", "definitions"):
        for sub in node.get(key, {}).values():
            _strictify(sub, nullable_optional=nullable_optional)
    for key in ("items", "anyOf", "oneOf", "allOf", "prefixItems"):
        _strictify(node.get(key), nullable_optional=nullable_optional)
    properties = node.get("properties")
    if isinstance(properties, dict):
        was_required = set(node.get("required", ()))
        for name, sub in properties.items():
            _strictify(sub, nullable_optional=nullable_optional)
            if nullable_optional and name not in was_required and not _is_nullable(sub):
                properties[name] = {"anyOf": [sub, {"type": "null"}]}
        node["required"] = list(properties)
        node["additionalProperties"] = False


def _strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """`model`'s JSON schema in the form OpenAI's strict structured output accepts. Optional
    fields become required but keep their schema: `X | None` fields are already nullable, and a
    default-factory list stays a non-null array, so a conforming response still validates."""
    schema = copy.deepcopy(model.model_json_schema())
    _strictify(schema, nullable_optional=False)
    return schema


def _function_tool(spec: dict[str, Any]) -> dict[str, Any]:
    """A `ToolRunner` spec as a Responses API function tool. Optional arguments become required
    and nullable; `_optional_args` names them so a null is dropped before the tool runs."""
    parameters = copy.deepcopy(spec["input_schema"])
    _strictify(parameters, nullable_optional=True)
    return {
        "type": "function",
        "name": spec["name"],
        "description": spec["description"],
        "parameters": parameters,
        "strict": True,
    }


def _optional_args(spec: dict[str, Any]) -> frozenset[str]:
    """The top-level arguments the spec does not require. `_function_tool` makes them required
    and nullable, so a None for one of them means "not given"."""
    schema = spec["input_schema"]
    return frozenset(set(schema.get("properties", {})) - set(schema.get("required", ())))


# --- The client -------------------------------------------------------------------------------


def _usage_of(response: Any) -> Usage:
    """Tokens for one response. `input_tokens` includes the cached (and cache-written) tokens,
    which are split out so each is priced at its own rate; reasoning tokens are already inside
    `output_tokens`."""
    model = str(response.model)
    u = response.usage
    if u is None:
        return Usage(model=model)
    details = getattr(u, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    written = getattr(details, "cache_write_tokens", 0) or 0
    return Usage(
        model=model,
        input=max((u.input_tokens or 0) - cached - written, 0),
        output=u.output_tokens or 0,
        cache_read=cached,
        cache_write=written,
    )


def _problem(response: Any) -> tuple[ErrorCode, str] | None:
    """The error a response that cannot be used maps to (failed, cut off, filtered or refused),
    or None."""
    if response.status == "failed":
        detail = getattr(response.error, "message", None)
        logger.warning("openai response failed: %s", detail)
        return "ai_unavailable", "The AI service is unavailable."
    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", None)
        if reason == "content_filter":
            return "refused", "The model declined this request."
        if reason == "max_output_tokens":
            return "truncated", "The response was cut off (max_output_tokens)."
        return "truncated", f"The response was cut off ({reason})."
    for item in response.output:
        if item.type == "message" and any(part.type == "refusal" for part in item.content):
            return "refused", "The model declined this request."
    return None


def _function_calls(response: Any) -> list[Any]:
    return [item for item in response.output if item.type == "function_call"]


@lru_cache
def _sdk_client(api_key: str) -> AsyncOpenAI:
    """One SDK client (one connection pool) per key, shared by every request."""
    return AsyncOpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=2)


class OpenAIClient:
    def __init__(self, settings: Settings, sdk: AsyncOpenAI | None = None) -> None:
        if sdk is None:
            if not settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY is not set")
            sdk = _sdk_client(settings.openai_api_key)
        self._settings = settings
        self._sdk = sdk

    def _request(
        self, tier: Tier, system: str, output_model: type[BaseModel] | None
    ) -> dict[str, Any]:
        """The parts of the request that stay the same for every round of the tool loop. With no
        `output_model` the reply is free text (no `text.format`). OpenAI caches the longest
        repeated prefix by itself, so the order matters: tools, then the stable instructions,
        then the couple's context (the first input item), then the messages."""
        s = self._settings
        smart = tier == "smart"
        request: dict[str, Any] = {
            "model": s.ai_model_smart if smart else s.ai_model_fast,
            "instructions": system,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "reasoning": {"effort": s.ai_effort_smart if smart else s.ai_effort_fast},
            # Nothing is kept on OpenAI's side; the reasoning items come back encrypted and are
            # replayed to it between the rounds of the tool loop.
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if output_model is not None:
            request["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": output_model.__name__,
                    "schema": _strict_schema(output_model),
                    "strict": True,
                }
            }
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
        request = self._request(tier, system, output_model)
        if specs:
            request["tools"] = [_function_tool(spec) for spec in specs]
        optional = {spec["name"]: _optional_args(spec) for spec in specs}
        convo: list[Any] = [{"role": "developer", "content": context}, *messages]
        max_rounds = self._settings.ai_max_tool_iterations
        usage = Usage(model=request["model"])
        tool_calls = 0

        def fail(code: ErrorCode, message: str) -> AIError:
            return AIError(code, message, usage=usage, tool_calls=tool_calls)

        # Up to `max_rounds` rounds of tool calls, then one request that must be the answer.
        for round_no in range(max_rounds + 1):
            try:
                response = await self._sdk.responses.create(**request, input=list(convo))
            except openai.APIError as exc:
                logger.warning("openai request failed: %s: %s", type(exc).__name__, exc)
                raise fail("ai_unavailable", "The AI service is unavailable.") from exc
            usage = usage + _usage_of(response)

            if (problem := _problem(response)) is not None:
                raise fail(*problem)

            calls = _function_calls(response)
            if calls:
                if round_no == max_rounds:
                    raise fail("truncated", "The tool-use iteration limit was reached.")
                if tools is None:
                    raise fail("ai_unavailable", "The model called a tool but none exist.")
                # Nothing is stored server-side, so the whole output (reasoning included) goes
                # back with the results.
                convo.extend(item.model_dump(exclude_none=True) for item in response.output)
                for call in calls:
                    tool_calls += 1
                    convo.append(
                        await self._call_tool(
                            tools, call.name, call.arguments, call.call_id, optional
                        )
                    )
                continue

            if response.status != "completed":
                raise fail("ai_unavailable", f"Unexpected status {response.status!r}.")
            try:
                output = output_model.model_validate_json(response.output_text)
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
        request = self._request("smart", system, None)
        if specs:
            request["tools"] = [_function_tool(spec) for spec in specs]
        optional = {spec["name"]: _optional_args(spec) for spec in specs}
        convo: list[Any] = [{"role": "developer", "content": context}, *messages]
        max_rounds = self._settings.ai_max_tool_iterations
        usage = Usage(model=request["model"])
        tool_calls = 0
        texts: list[str] = []

        def fail(code: ErrorCode, message: str) -> AIError:
            return AIError(code, message, usage=usage, tool_calls=tool_calls)

        for round_no in range(max_rounds + 1):
            response = None
            try:
                async with self._sdk.responses.stream(**request, input=list(convo)) as stream:
                    async for event in stream:
                        if event.type == "response.output_text.delta":
                            yield TextDelta(event.delta)
                        elif (
                            event.type == "response.completed"
                            or event.type == "response.incomplete"
                            or event.type == "response.failed"
                        ):
                            response = event.response
            except Exception as exc:  # a dropped stream can also raise raw httpx errors
                logger.warning("openai stream failed: %s: %s", type(exc).__name__, exc)
                raise fail("ai_unavailable", "The AI service is unavailable.") from exc
            if response is None:
                # Not get_final_response(): it raises unless the stream completed, which would
                # hide an incomplete (truncated/refused) or failed response and its usage.
                logger.warning("openai stream ended without a terminal event")
                raise fail("ai_unavailable", "The AI service is unavailable.")
            usage = usage + _usage_of(response)
            texts.append(response.output_text)

            if (problem := _problem(response)) is not None:
                raise fail(*problem)

            calls = _function_calls(response)
            if calls:
                if round_no == max_rounds:
                    raise fail("truncated", "The tool-use iteration limit was reached.")
                if tools is None:
                    raise fail("ai_unavailable", "The model called a tool but none exist.")
                convo.extend(item.model_dump(exclude_none=True) for item in response.output)
                for call in calls:
                    tool_calls += 1
                    yield ToolStarted(call.name)
                    result = await self._call_tool(
                        tools, call.name, call.arguments, call.call_id, optional
                    )
                    yield ToolFinished(call.name)
                    convo.append(result)
                continue

            if response.status != "completed":
                raise fail("ai_unavailable", f"Unexpected status {response.status!r}.")
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
        tools: ToolRunner,
        name: str,
        arguments: str,
        call_id: str,
        optional: dict[str, frozenset[str]],
    ) -> dict[str, Any]:
        try:
            args = json.loads(arguments)
            if not isinstance(args, dict):
                raise ValueError("tool arguments are not an object")
            # Optional arguments are sent as null (strict mode needs them all); handlers expect
            # them absent.
            for key in optional.get(name, ()):
                if args.get(key) is None:
                    args.pop(key, None)
            output = await tools.execute(name, args)
        except Exception:
            # A broken tool must not end the run: tell the model, which can carry on without.
            logger.exception("tool %s failed", name)
            output = json.dumps({"error": f"{name} failed"})
        return {"type": "function_call_output", "call_id": call_id, "output": output}


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
    """OpenAI when the key is set (with or without DEMO_MODE), the fake in DEMO_MODE,
    otherwise None: AI is unavailable."""
    if settings.openai_api_key:
        return OpenAIClient(settings)
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
