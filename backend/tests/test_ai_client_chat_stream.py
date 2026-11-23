"""`chat_stream`: the streaming free-text tool loop of `OpenAIClient` against a stub SDK (no
network), and the fake client's chat path."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from app.ai.client import (
    AIError,
    ChatEvent,
    ChatFinished,
    FakeAIClient,
    OpenAIClient,
    TextDelta,
    ToolFinished,
    ToolRunner,
    ToolStarted,
    Usage,
)
from tests.test_ai_client import (
    ChatToolRunner,
    Runner,
    client_for,
    refusal,
    reply,
    text,
    tool_call,
)


def delta(body: str) -> Any:
    return SimpleNamespace(type="response.output_text.delta", delta=body)


# Not a text delta: must not be surfaced.
REASONING = SimpleNamespace(type="response.reasoning_summary_text.delta", delta="hmm")


class Round:
    """One scripted streaming round: the raw events, then (if there is a final response) the
    terminal event the API sends for its status, and (optionally) an error raised after that."""

    def __init__(self, events: list[Any], final: Any = None, *, error: Exception | None = None):
        self.events = list(events)
        if final is not None:
            self.events.append(SimpleNamespace(type=f"response.{final.status}", response=final))
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


class StreamSDK:
    """Stands in for AsyncOpenAI: `responses.stream` pops the next scripted round."""

    def __init__(self, *script: Round) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.responses = self

    def stream(self, **kwargs: Any) -> StubStream:
        self.requests.append({**kwargs, "input": list(kwargs["input"])})
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


def chat_client(sdk: StreamSDK, **settings: Any) -> OpenAIClient:
    return client_for(sdk, **settings)  # type: ignore[arg-type]


# --- OpenAIClient.chat_stream -------------------------------------------------------------


async def test_text_deltas_stream_in_order_and_finish_carries_text_and_usage() -> None:
    sdk = StreamSDK(
        Round(
            [delta("Hel"), REASONING, delta("lo "), delta("there")],
            reply([text("Hello there")], cached=40, written=7),
        )
    )
    events = await collect(chat_client(sdk))
    assert events == [
        TextDelta("Hel"),
        TextDelta("lo "),
        TextDelta("there"),
        ChatFinished(
            text="Hello there",
            usage=Usage(model="gpt-6.2-sol", input=53, output=10, cache_read=40, cache_write=7),
            tool_calls=0,
            degraded=set(),
        ),
    ]


async def test_request_is_free_text_on_the_smart_tier() -> None:
    sdk = StreamSDK(Round([delta("x")], reply([text("x")])))
    await collect(chat_client(sdk), Runner())

    [req] = sdk.requests
    assert req["model"] == "gpt-6.2-sol"
    assert req["instructions"] == "SYSTEM"
    assert req["reasoning"] == {"effort": "medium"}
    assert req["store"] is False and req["include"] == ["reasoning.encrypted_content"]
    assert "text" not in req  # effort, but no structured format
    assert [tool["name"] for tool in req["tools"]] == ["search_places"]
    assert req["input"] == [
        {"role": "developer", "content": '{"ctx":1}'},
        {"role": "user", "content": "hi"},
    ]


async def test_tool_round_emits_tool_events_and_feeds_the_result_back() -> None:
    first = reply(
        [text("Let me look. "), tool_call("call_1", "search_places", {"query": "pottery"})],
        input_tokens=100,
    )
    sdk = StreamSDK(
        Round([delta("Let me look. ")], first),
        Round([delta("Found one.")], reply([text("Found one.")], input_tokens=200)),
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

    # The second request replays the first round's output, then the tool result.
    assert sdk.requests[1]["input"][2:] == [
        *(item.model_dump(exclude_none=True) for item in first.output),
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"data":[{"name":"Clay Co"}]}',
        },
    ]


async def test_null_arguments_the_spec_requires_are_kept() -> None:
    sdk = StreamSDK(
        Round(
            [], reply([tool_call("call_1", "propose_idea", {"title": "Picnic", "est_cost": None})])
        ),
        Round([delta("ok")], reply([text("ok")])),
    )
    runner = ChatToolRunner()
    await collect(chat_client(sdk), runner)
    assert runner.calls == [("propose_idea", {"title": "Picnic", "est_cost": None})]


async def test_broken_tool_is_reported_to_the_model_not_raised() -> None:
    sdk = StreamSDK(
        Round([], reply([tool_call("call_1", "search_places", {"query": "x"})])),
        Round([delta("ok")], reply([text("ok")])),
    )
    runner = Runner()
    runner.fail = True
    events = await collect(chat_client(sdk), runner)
    assert ToolFinished("search_places") in events
    assert sdk.requests[1]["input"][-1]["output"] == '{"error": "search_places failed"}'


async def test_refusal_maps_to_refused_after_partial_text() -> None:
    sdk = StreamSDK(Round([delta("I can")], reply([refusal()], output_tokens=0)))
    seen: list[ChatEvent] = []
    with pytest.raises(AIError) as caught:
        async for event in chat_client(sdk).chat_stream(
            system="S", context="C", messages=[{"role": "user", "content": "hi"}], tools=None
        ):
            seen.append(event)
    assert seen == [TextDelta("I can")]
    assert caught.value.code == "refused"
    assert caught.value.usage == Usage(model="gpt-6.2-sol", input=100)


async def test_content_filter_maps_to_refused() -> None:
    final = reply([], status="incomplete", incomplete_reason="content_filter")
    with pytest.raises(AIError) as caught:
        await collect(chat_client(StreamSDK(Round([], final))))
    assert caught.value.code == "refused"


async def test_max_output_tokens_maps_to_truncated() -> None:
    final = reply(
        [text("a")], status="incomplete", incomplete_reason="max_output_tokens", output_tokens=16000
    )
    with pytest.raises(AIError) as caught:
        await collect(chat_client(StreamSDK(Round([delta("a")], final))))
    assert caught.value.code == "truncated"


async def test_failed_status_maps_to_ai_unavailable() -> None:
    final = reply([], status="failed", error={"code": "server_error", "message": "oops"})
    with pytest.raises(AIError) as caught:
        await collect(chat_client(StreamSDK(Round([], final))))
    assert caught.value.code == "ai_unavailable"


async def test_terminal_event_usage_is_kept_when_the_round_is_refused_or_truncated() -> None:
    final = reply([], status="incomplete", incomplete_reason="content_filter", input_tokens=77)
    with pytest.raises(AIError) as caught:
        await collect(chat_client(StreamSDK(Round([delta("a")], final))))
    assert caught.value.code == "refused"
    assert caught.value.usage == Usage(model="gpt-6.2-sol", input=77, output=10)

    final = reply(
        [text("a")], status="incomplete", incomplete_reason="max_output_tokens", input_tokens=55
    )
    with pytest.raises(AIError) as caught:
        await collect(chat_client(StreamSDK(Round([delta("a")], final))))
    assert caught.value.code == "truncated"
    assert caught.value.usage is not None and caught.value.usage.input == 55


async def test_failed_terminal_event_keeps_usage() -> None:
    final = reply([], status="failed", error={"code": "server_error", "message": "oops"})
    with pytest.raises(AIError) as caught:
        await collect(chat_client(StreamSDK(Round([], final))))
    assert caught.value.code == "ai_unavailable"
    assert caught.value.usage == Usage(model="gpt-6.2-sol", input=100, output=10)


async def test_a_stream_that_ends_without_a_terminal_event_is_ai_unavailable() -> None:
    sdk = StreamSDK(Round([delta("par")]))
    seen: list[ChatEvent] = []
    with pytest.raises(AIError) as caught:
        async for event in chat_client(sdk).chat_stream(
            system="S", context="C", messages=[{"role": "user", "content": "hi"}], tools=None
        ):
            seen.append(event)
    assert seen == [TextDelta("par")]
    assert caught.value.code == "ai_unavailable"


@pytest.mark.parametrize(
    "boom",
    [
        openai.APIConnectionError(request=httpx.Request("POST", "https://x.test")),
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
        Round([], reply([tool_call(f"call_{i}", "search_places", {"query": "x"})]))
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
