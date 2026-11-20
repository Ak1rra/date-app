"""The AI client: tool loop, limits, error mapping and request shape, against a fake SDK (no
network), plus the fake client and client selection."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from app.ai.client import (
    FAKE_RESPONDERS,
    AIClient,
    AIError,
    AnthropicAIClient,
    FakeAIClient,
    ToolRunner,
    Usage,
    get_ai,
    get_ai_client,
)
from app.errors import ApiError
from tests.conftest import make_settings


class Pick(BaseModel):
    title: str
    n: int


PICK_JSON = '{"title":"Pottery","n":3}'


def reply(
    stop_reason: str,
    content: list[dict[str, Any]],
    *,
    model: str = "claude-sonnet-5-5",
    input_tokens: int = 100,
    output_tokens: int = 10,
    cache_read: int = 0,
    cache_write: int = 0,
) -> Any:
    from anthropic.types.beta import BetaMessage

    return BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        }
    )


def text(body: str) -> dict[str, Any]:
    return {"type": "text", "text": body}


def tool_use(tool_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


class FakeSDK:
    """Stands in for AsyncAnthropic: `beta.messages.create` pops the next scripted response
    (or raises it) and records the request as it was at call time."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.beta = self
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Runner:
    """A ToolRunner that records its calls."""

    def __init__(self, results: dict[str, str] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.degraded: set[str] = set()
        self.fail = False

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_places",
                "description": "Find places.",
                "strict": True,
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }
        ]

    async def execute(self, name: str, input: dict[str, Any]) -> str:
        self.calls.append((name, input))
        if self.fail:
            raise RuntimeError("boom")
        return self.results.get(name, '{"data":[]}')


def client_for(sdk: FakeSDK, **settings: Any) -> AnthropicAIClient:
    cfg = make_settings(demo_mode=False, anthropic_api_key="test-key", **settings)
    return AnthropicAIClient(cfg, sdk=sdk)  # type: ignore[arg-type]


async def run(client: AIClient, tools: ToolRunner | None = None, tier: Any = "smart") -> Any:
    return await client.run(
        tier=tier,
        system="SYSTEM",
        context='{"ctx":1}',
        messages=[{"role": "user", "content": "plan"}],
        tools=tools,
        output_model=Pick,
    )


# --- AnthropicAIClient --------------------------------------------------------------------


async def test_single_turn_smart_request_shape_and_result() -> None:
    sdk = FakeSDK(reply("end_turn", [text(PICK_JSON)], cache_read=40, cache_write=7))
    result = await run(client_for(sdk))
    assert result.output == Pick(title="Pottery", n=3)
    assert result.usage == Usage(
        model="claude-sonnet-5-5", input=100, output=10, cache_read=40, cache_write=7
    )
    assert result.tool_calls == 0 and result.degraded == set()

    [req] = sdk.requests
    assert req["model"] == "claude-sonnet-5-5"
    assert req["thinking"] == {"type": "adaptive"}
    # No request-level caching: the breakpoint ends the stable system prompt, and the context
    # block after it is unmarked so a changed context leaves the cached prefix alone.
    assert "cache_control" not in req
    assert req["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": '{"ctx":1}'},
    ]
    assert "tools" not in req
    # Smart tier: effort from config, structured output, refusal fallback.
    assert req["output_config"]["effort"] == "medium"
    fmt = req["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == {"title", "n"}
    assert req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["fallbacks"] == "default"
    # The last message block carries the second breakpoint (a plain string becomes one block).
    assert req["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "plan", "cache_control": {"type": "ephemeral"}}],
        }
    ]


async def test_fast_tier_uses_the_fast_model_and_effort_without_fallback() -> None:
    sdk = FakeSDK(reply("end_turn", [text(PICK_JSON)], model="claude-haiku-5-5"))
    result = await run(client_for(sdk), tier="fast")
    [req] = sdk.requests
    assert req["model"] == "claude-haiku-5-5"
    assert req["output_config"]["effort"] == "low"
    assert "betas" not in req and "fallbacks" not in req
    assert result.usage.model == "claude-haiku-5-5"


async def test_tool_loop_executes_tools_and_sums_usage() -> None:
    first = reply(
        "tool_use",
        [
            text("Let me look."),
            tool_use("tu_1", "search_places", {"query": "pottery"}),
            tool_use("tu_2", "search_places", {"query": "cafe"}),
        ],
        input_tokens=100,
        output_tokens=20,
        cache_write=500,
    )
    second = reply("end_turn", [text(PICK_JSON)], input_tokens=30, output_tokens=15, cache_read=500)
    sdk = FakeSDK(first, second)
    runner = Runner({"search_places": '{"data":[{"place_id":"p1"}]}'})
    runner.degraded = {"events"}

    result = await run(client_for(sdk), runner)

    assert result.tool_calls == 2
    assert runner.calls == [
        ("search_places", {"query": "pottery"}),
        ("search_places", {"query": "cafe"}),
    ]
    assert result.degraded == {"events"}
    assert result.usage == Usage(
        model="claude-sonnet-5-5", input=130, output=35, cache_read=500, cache_write=500
    )
    # Tools go in the request, and the second request carries the assistant turn and one user
    # message holding every tool result.
    assert sdk.requests[0]["tools"] == runner.specs()
    assistant, results = sdk.requests[1]["messages"][1:]
    assert assistant["role"] == "assistant" and assistant["content"] == first.content
    assert results == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tu_1",
                "content": '{"data":[{"place_id":"p1"}]}',
            },
            {
                "type": "tool_result",
                "tool_use_id": "tu_2",
                "content": '{"data":[{"place_id":"p1"}]}',
                "cache_control": {"type": "ephemeral"},
            },
        ],
    }


def markers(request: dict[str, Any]) -> list[tuple[int, int]]:
    """(message index, block index) of every cache breakpoint in the request's messages."""
    found = []
    for m, message in enumerate(request["messages"]):
        content = message["content"]
        if isinstance(content, str):
            continue
        for b, block in enumerate(content):
            if isinstance(block, dict) and "cache_control" in block:
                found.append((m, b))
    return found


async def test_callers_may_not_set_cache_control() -> None:
    sdk = FakeSDK()
    client = client_for(sdk)
    marked = {"type": "text", "text": "plan", "cache_control": {"type": "ephemeral"}}
    with pytest.raises(ValueError, match="callers must not set cache_control"):
        await client.run(
            tier="smart",
            system="S",
            context="{}",
            messages=[{"role": "user", "content": [marked]}],
            tools=None,
            output_model=Pick,
        )

    class MarkedTools(Runner):
        def specs(self) -> list[dict[str, Any]]:
            return [{**super().specs()[0], "cache_control": {"type": "ephemeral"}}]

    with pytest.raises(ValueError, match="callers must not set cache_control"):
        await run(client, MarkedTools())
    assert sdk.requests == []  # rejected before anything was sent


async def test_each_tool_loop_request_has_one_breakpoint_on_its_last_block() -> None:
    sdk = FakeSDK(
        reply("tool_use", [tool_use("tu_1", "search_places", {"query": "a"})]),
        reply(
            "tool_use",
            [
                tool_use("tu_2", "search_places", {"query": "b"}),
                tool_use("tu_3", "search_places", {"query": "c"}),
            ],
        ),
        reply("end_turn", [text(PICK_JSON)]),
    )
    messages = [{"role": "user", "content": "plan"}]
    await client_for(sdk).run(
        tier="smart",
        system="SYSTEM",
        context="{}",
        messages=messages,
        tools=Runner(),
        output_model=Pick,
    )

    assert [markers(r) for r in sdk.requests] == [[(0, 0)], [(2, 0)], [(4, 1)]]
    for request in sdk.requests:
        assert request["messages"][-1]["role"] == "user"
        assert "cache_control" not in request  # the top-level one is gone
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in request["system"][1]
    # The stored history carries none: the caller's own messages are untouched, and the third
    # request (which re-sent rounds 1 and 2) still has just its one.
    assert messages == [{"role": "user", "content": "plan"}]


async def test_fallback_attempts_in_usage_iterations_are_summed_not_double_counted() -> None:
    response = reply("end_turn", [text(PICK_JSON)], input_tokens=60, output_tokens=5)
    response.usage.iterations = [
        SimpleNamespace(
            input_tokens=100,
            output_tokens=0,
            cache_read_input_tokens=10,
            cache_creation_input_tokens=0,
        ),
        SimpleNamespace(
            input_tokens=60,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=7,
        ),
    ]
    result = await run(client_for(FakeSDK(response)))
    # The top-level 60/5 is the second attempt, which the list already holds.
    assert result.usage == Usage(
        model="claude-sonnet-5-5", input=160, output=5, cache_read=10, cache_write=7
    )
    # Each attempt is kept as its own priced line; one with no model of its own takes the
    # response's.
    assert result.usage.lines == (
        ("claude-sonnet-5-5", 100, 0, 10, 0),
        ("claude-sonnet-5-5", 60, 5, 0, 7),
    )


async def test_fallback_iterations_keep_the_model_of_each_attempt() -> None:
    response = reply("end_turn", [text(PICK_JSON)], model="claude-sonnet-5")
    response.usage.iterations = [
        SimpleNamespace(model="claude-sonnet-5-5", input_tokens=1000, output_tokens=0),
        SimpleNamespace(model="claude-sonnet-5", input_tokens=1000, output_tokens=500),
    ]
    result = await run(client_for(FakeSDK(response)))
    assert result.usage.model == "claude-sonnet-5"
    assert result.usage.lines == (
        ("claude-sonnet-5-5", 1000, 0, 0, 0),
        ("claude-sonnet-5", 1000, 500, 0, 0),
    )


def test_usage_add_keeps_every_priced_line_across_rounds() -> None:
    fallback = Usage(
        "claude-sonnet-5",
        input=2000,
        lines=(("claude-sonnet-5-5", 1000, 0, 0, 0), ("claude-sonnet-5", 1000, 0, 0, 0)),
    )
    plain = Usage("claude-sonnet-5-5", input=5, output=1)
    total = Usage(model="claude-sonnet-5-5") + fallback + plain
    assert (total.model, total.input, total.output) == ("claude-sonnet-5-5", 2005, 1)
    assert total.lines == (
        ("claude-sonnet-5-5", 1000, 0, 0, 0),
        ("claude-sonnet-5", 1000, 0, 0, 0),
        ("claude-sonnet-5-5", 5, 1, 0, 0),
    )
    assert Usage("claude-sonnet-5-5").priced_lines() == ()  # nothing used, nothing to price


async def test_missing_or_empty_usage_iterations_fall_back_to_the_top_level_counts() -> None:
    plain = reply("end_turn", [text(PICK_JSON)], input_tokens=60, output_tokens=5, cache_read=3)
    empty = reply("end_turn", [text(PICK_JSON)], input_tokens=60, output_tokens=5, cache_read=3)
    empty.usage.iterations = []
    for response in (plain, empty):
        result = await run(client_for(FakeSDK(response)))
        assert result.usage == Usage(model="claude-sonnet-5-5", input=60, output=5, cache_read=3)


async def test_tool_failure_is_reported_to_the_model_not_raised() -> None:
    sdk = FakeSDK(
        reply("tool_use", [tool_use("tu_1", "search_places", {"query": "x"})]),
        reply("end_turn", [text(PICK_JSON)]),
    )
    runner = Runner()
    runner.fail = True
    result = await run(client_for(sdk), runner)
    assert result.output.title == "Pottery"
    [tool_result] = sdk.requests[1]["messages"][2]["content"]
    assert tool_result["is_error"] is True and tool_result["tool_use_id"] == "tu_1"


async def test_iteration_cap_raises_truncated_with_usage_so_far() -> None:
    looping = [
        reply("tool_use", [tool_use(f"tu_{i}", "search_places", {"query": "x"})]) for i in range(3)
    ]
    sdk = FakeSDK(*looping)
    runner = Runner()
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk, ai_max_tool_iterations=2), runner)
    assert caught.value.code == "truncated"
    # Two rounds of tools, then the third request had to be the answer and was not.
    assert len(sdk.requests) == 3 and len(runner.calls) == 2
    assert caught.value.tool_calls == 2
    assert caught.value.usage is not None and caught.value.usage.input == 300


async def test_refusal_maps_to_refused() -> None:
    sdk = FakeSDK(
        reply(
            "refusal",
            [],
            output_tokens=0,
        )
    )
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "refused"
    assert caught.value.usage == Usage(model="claude-sonnet-5-5", input=100)


async def test_max_tokens_maps_to_truncated() -> None:
    sdk = FakeSDK(reply("max_tokens", [text('{"title":"Pott')], output_tokens=16000))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "truncated"
    assert caught.value.usage is not None and caught.value.usage.output == 16000


async def test_sdk_errors_map_to_ai_unavailable_keeping_usage() -> None:
    boom = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.test"))
    sdk = FakeSDK(reply("tool_use", [tool_use("tu_1", "search_places", {"query": "x"})]), boom)
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk), Runner())
    assert caught.value.code == "ai_unavailable"
    assert caught.value.tool_calls == 1 and caught.value.usage is not None
    assert caught.value.usage.input == 100


async def test_output_that_fails_validation_is_invalid_output() -> None:
    sdk = FakeSDK(reply("end_turn", [text('{"title":"x"}')]))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "invalid_output"


# --- FakeAIClient and selection -----------------------------------------------------------


async def test_fake_client_calls_responder_with_counted_tools_and_zero_usage() -> None:
    seen: dict[str, Any] = {}

    async def responder(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> BaseModel:
        assert tools is not None
        seen["result"] = await tools.execute("search_places", {"query": "q"})
        seen["context"], seen["messages"] = context, messages
        return Pick(title="Fake", n=1)

    runner = Runner({"search_places": '{"data":["p"]}'})
    runner.degraded = {"weather"}
    result = await run(FakeAIClient({Pick: responder}), runner)

    assert result.output == Pick(title="Fake", n=1)
    assert result.usage == Usage(model="fake")
    assert result.tool_calls == 1 and result.degraded == {"weather"}
    assert seen["result"] == '{"data":["p"]}' and seen["context"] == '{"ctx":1}'
    assert runner.calls == [("search_places", {"query": "q"})]


async def test_fake_client_responder_errors_become_ai_errors() -> None:
    boom = RuntimeError("boom")

    async def crashing(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> BaseModel:
        raise boom

    with pytest.raises(AIError) as caught:
        await run(FakeAIClient({Pick: crashing}))
    assert caught.value.code == "ai_unavailable"
    assert caught.value.__cause__ is boom

    refusal = AIError("refused", "no")

    async def refusing(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> BaseModel:
        raise refusal

    with pytest.raises(AIError) as caught:
        await run(FakeAIClient({Pick: refusing}))
    assert caught.value is refusal  # an AIError passes through untouched


async def test_fake_client_without_a_responder_is_unavailable() -> None:
    with pytest.raises(AIError) as caught:
        await run(FakeAIClient({}))
    assert caught.value.code == "ai_unavailable"


def test_get_ai_client_selection() -> None:
    keyed = make_settings(demo_mode=True, anthropic_api_key="k")
    assert isinstance(get_ai_client(keyed), AnthropicAIClient)

    demo = get_ai_client(make_settings(demo_mode=True, anthropic_api_key=None))
    assert isinstance(demo, FakeAIClient)
    assert demo.responders is FAKE_RESPONDERS  # callers extend the shared map

    assert get_ai_client(make_settings(demo_mode=False, anthropic_api_key=None)) is None


def test_get_ai_dependency_is_503_when_unavailable() -> None:
    with pytest.raises(ApiError) as caught:
        get_ai(make_settings(demo_mode=False, anthropic_api_key=None))
    assert caught.value.status == 503 and caught.value.code == "ai_unavailable"
    assert isinstance(get_ai(make_settings(demo_mode=True, anthropic_api_key=None)), FakeAIClient)
