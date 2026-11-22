"""`chat_stream`: the streaming free-text tool loop of `AnthropicAIClient` against a stub SDK (no
network), and the fake client's chat path."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from app.ai.client import (
    AIError,
    AnthropicAIClient,
    ChatEvent,
    ChatFinished,
    FakeAIClient,
    TextDelta,
    ToolFinished,
    ToolRunner,
    ToolStarted,
    Usage,
)
from tests.test_ai_client import Runner, client_for, reply, text, tool_use


def delta(body: str) -> Any:
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=body)
    )


# Not a text delta: must not be surfaced.
THINKING = SimpleNamespace(
    type="content_block_delta", delta=SimpleNamespace(type="thinking_delta", thinking="hmm")
)


class Round:
    """One scripted streaming round: the raw events, the final message, and (optionally) an
    error raised after the events."""

    def __init__(self, events: list[Any], final: Any = None, *, error: Exception | None = None):
        self.events = events
        self.final = final
        self.error = error


class StubStream:
    def __init__(self, scripted: Round) -> None:
        self.scripted = scripted

    async def __aenter__(self) -> "StubStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._events()

    async def _events(self) -> AsyncIterator[Any]:
        for event in self.scripted.events:
            yield event
        if self.scripted.error is not None:
            raise self.scripted.error

    async def get_final_message(self) -> Any:
        return self.scripted.final


class StreamSDK:
    """Stands in for AsyncAnthropic: `beta.messages.stream` pops the next scripted round."""

    def __init__(self, *script: Round) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.beta = self
        self.messages = self

    def stream(self, **kwargs: Any) -> StubStream:
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return StubStream(self.script.pop(0))


async def collect(
    client: Any, tools: ToolRunner | None = None, messages: list[dict[str, Any]] | None = None
) -> list[ChatEvent]:
    return [
        event
        async for event in client.chat_stream(
            system="SYSTEM",
            context='{"ctx":1}',
            messages=messages or [{"role": "user", "content": "hi"}],
            tools=tools,
        )
    ]


def chat_client(sdk: StreamSDK, **settings: Any) -> AnthropicAIClient:
    return client_for(sdk, **settings)  # type: ignore[arg-type]


# --- AnthropicAIClient.chat_stream --------------------------------------------------------


async def test_text_deltas_stream_in_order_and_finish_carries_text_and_usage() -> None:
    sdk = StreamSDK(
        Round(
            [delta("Hel"), THINKING, delta("lo "), delta("there")],
            reply("end_turn", [text("Hello there")], cache_read=40, cache_write=7),
        )
    )
    events = await collect(chat_client(sdk))
    assert events == [
        TextDelta("Hel"),
        TextDelta("lo "),
        TextDelta("there"),
        ChatFinished(
            text="Hello there",
            usage=Usage(
                model="claude-sonnet-5-5", input=100, output=10, cache_read=40, cache_write=7
            ),
            tool_calls=0,
            degraded=set(),
        ),
    ]


async def test_request_is_free_text_smart_with_thinking_and_cache_markers() -> None:
    sdk = StreamSDK(Round([delta("x")], reply("end_turn", [text("x")])))
    await collect(chat_client(sdk), Runner())

    [req] = sdk.requests
    assert req["model"] == "claude-sonnet-5-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_config"] == {"effort": "medium"}  # effort, but no structured format
    assert req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["fallbacks"] == "default"
    assert req["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": '{"ctx":1}'},
    ]
    assert [spec["name"] for spec in req["tools"]] == ["search_places"]
    assert req["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
        }
    ]


async def test_tool_round_emits_tool_events_and_feeds_the_result_back() -> None:
    sdk = StreamSDK(
        Round(
            [delta("Let me look. ")],
            reply(
                "tool_use",
                [text("Let me look. "), tool_use("tu_1", "search_places", {"query": "pottery"})],
                input_tokens=100,
            ),
        ),
        Round([delta("Found one.")], reply("end_turn", [text("Found one.")], input_tokens=200)),
    )
    runner = Runner({"search_places": '{"data":[{"name":"Clay Co"}]}'})
    events = await collect(chat_client(sdk), runner)

    assert events[:4] == [
        TextDelta("Let me look. "),
        ToolStarted("search_places"),
        ToolFinished("search_places"),
        TextDelta("Found one."),
    ]
    finished = events[4]
    assert isinstance(finished, ChatFinished)
    # What was streamed is what is stored: the text of every round.
    assert finished.text == "Let me look. Found one."
    assert finished.tool_calls == 1
    assert finished.usage.input == 300
    assert runner.calls == [("search_places", {"query": "pottery"})]

    second = sdk.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    [result] = second[2]["content"]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
    assert result["content"] == '{"data":[{"name":"Clay Co"}]}'


async def test_broken_tool_is_reported_to_the_model_not_raised() -> None:
    sdk = StreamSDK(
        Round([], reply("tool_use", [tool_use("tu_1", "search_places", {"query": "x"})])),
        Round([delta("ok")], reply("end_turn", [text("ok")])),
    )
    runner = Runner()
    runner.fail = True
    events = await collect(chat_client(sdk), runner)
    assert ToolFinished("search_places") in events
    [result] = sdk.requests[1]["messages"][2]["content"]
    assert result["is_error"] is True


async def test_refusal_maps_to_refused_after_partial_text() -> None:
    sdk = StreamSDK(Round([delta("I can")], reply("refusal", [], output_tokens=0)))
    seen: list[ChatEvent] = []
    with pytest.raises(AIError) as caught:
        async for event in chat_client(sdk).chat_stream(
            system="S", context="C", messages=[{"role": "user", "content": "hi"}], tools=None
        ):
            seen.append(event)
    assert seen == [TextDelta("I can")]
    assert caught.value.code == "refused"
    assert caught.value.usage == Usage(model="claude-sonnet-5-5", input=100)


async def test_max_tokens_maps_to_truncated() -> None:
    sdk = StreamSDK(Round([delta("a")], reply("max_tokens", [text("a")], output_tokens=16000)))
    with pytest.raises(AIError) as caught:
        await collect(chat_client(sdk))
    assert caught.value.code == "truncated"


@pytest.mark.parametrize(
    "boom",
    [
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.test")),
        httpx.RemoteProtocolError("peer closed connection"),
        RuntimeError("stream ended early"),
    ],
)
async def test_api_error_before_and_mid_stream_maps_to_ai_unavailable(boom: Exception) -> None:
    sdk = StreamSDK(Round([delta("par")], error=boom))
    seen: list[ChatEvent] = []
    with pytest.raises(AIError) as caught:
        async for event in chat_client(sdk).chat_stream(
            system="S", context="C", messages=[{"role": "user", "content": "hi"}], tools=None
        ):
            seen.append(event)
    assert seen == [TextDelta("par")]
    assert caught.value.code == "ai_unavailable"


async def test_tool_round_limit_raises_truncated_with_usage_so_far() -> None:
    looping = [
        Round([], reply("tool_use", [tool_use(f"tu_{i}", "search_places", {"query": "x"})]))
        for i in range(3)
    ]
    sdk = StreamSDK(*looping)
    runner = Runner()
    with pytest.raises(AIError) as caught:
        await collect(chat_client(sdk, ai_max_tool_iterations=2), runner)
    assert caught.value.code == "truncated"
    assert len(sdk.requests) == 3 and len(runner.calls) == 2
    assert caught.value.tool_calls == 2
    assert caught.value.usage is not None and caught.value.usage.input == 300


async def test_caller_cache_control_is_rejected() -> None:
    sdk = StreamSDK()
    marked = [{"role": "user", "content": [{"type": "text", "text": "x", "cache_control": {}}]}]
    with pytest.raises(ValueError, match="cache_control"):
        await collect(chat_client(sdk), messages=marked)


# --- FakeAIClient.chat_stream -------------------------------------------------------------


class IdeaRunner(Runner):
    def specs(self) -> list[dict[str, Any]]:
        return [{"name": "propose_idea"}]


async def test_fake_default_streams_a_canned_reply() -> None:
    events = await collect(FakeAIClient(), Runner(), [{"role": "user", "content": "idea please"}])
    # The idea tool is not offered, so nothing is proposed.
    deltas = [e for e in events if isinstance(e, TextDelta)]
    assert 3 <= len(deltas) <= 4
    assert not any(isinstance(e, ToolStarted) for e in events)
    finished = events[-1]
    assert isinstance(finished, ChatFinished)
    assert finished.text == "".join(d.text for d in deltas)
    assert finished.usage == Usage(model="fake") and finished.tool_calls == 0


async def test_fake_default_proposes_an_idea_when_asked_and_the_tool_exists() -> None:
    runner = IdeaRunner()
    events = await collect(
        FakeAIClient(),
        runner,
        [{"role": "user", "content": [{"type": "text", "text": "An IDEA?"}]}],
    )
    assert events[0] == ToolStarted("propose_idea")
    assert events[1] == ToolFinished("propose_idea")
    [(name, args)] = runner.calls
    assert name == "propose_idea" and args["title"] == "Sunset picnic"
    assert args["category"] == "outdoors" and args["best_time"] == "evening"
    assert args["indoor_outdoor"] == "outdoor"
    finished = events[-1]
    assert isinstance(finished, ChatFinished) and finished.tool_calls == 1


async def test_fake_default_does_not_propose_without_the_word_idea() -> None:
    runner = IdeaRunner()
    events = await collect(FakeAIClient(), runner, [{"role": "user", "content": "hello"}])
    assert runner.calls == [] and not any(isinstance(e, ToolStarted) for e in events)


async def test_fake_custom_responder_delegates_and_fixes_tool_calls() -> None:
    async def responder(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        assert tools is not None and context == '{"ctx":1}'
        yield ToolStarted("search_places")
        await tools.execute("search_places", {"query": "x"})
        yield ToolFinished("search_places")
        yield TextDelta("done")
        yield ChatFinished(text="done", usage=Usage(model="fake"), tool_calls=99, degraded=set())

    runner = Runner()
    events = await collect(FakeAIClient(chat_responder=responder), runner)
    finished = events[-1]
    assert isinstance(finished, ChatFinished) and finished.tool_calls == 1
    assert runner.calls == [("search_places", {"query": "x"})]


async def test_fake_wraps_unexpected_responder_errors_and_passes_ai_errors_through() -> None:
    async def broken(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        yield TextDelta("a")
        raise RuntimeError("boom")

    async def refusing(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        raise AIError("refused")
        yield  # pragma: no cover

    with pytest.raises(AIError) as caught:
        await collect(FakeAIClient(chat_responder=broken))
    assert caught.value.code == "ai_unavailable"
    with pytest.raises(AIError) as caught:
        await collect(FakeAIClient(chat_responder=refusing))
    assert caught.value.code == "refused"
