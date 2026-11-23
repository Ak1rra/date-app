"""The AI client: tool loop, limits, error mapping and request shape, against a fake SDK (no
network), plus the schema/tool conversion helpers, the fake client and client selection."""

import json
from typing import Any

import httpx
import openai
import pytest
from openai.types.responses import Response
from pydantic import BaseModel, Field

from app.ai.client import (
    FAKE_RESPONDERS,
    AIClient,
    AIError,
    FakeAIClient,
    OpenAIClient,
    ToolRunner,
    Usage,
    _function_tool,
    _strict_schema,
    get_ai,
    get_ai_client,
)
from app.ai.schemas import GeneratedIdeas, PlanOptions
from app.ai.tools import TOOL_SPECS
from app.errors import ApiError
from tests.conftest import make_settings


class Pick(BaseModel):
    title: str
    n: int


PICK_JSON = '{"title":"Pottery","n":3}'


def reply(
    output: list[dict[str, Any]],
    *,
    status: str = "completed",
    incomplete_reason: str | None = None,
    error: dict[str, Any] | None = None,
    model: str = "gpt-6.2-sol",
    input_tokens: int = 100,
    output_tokens: int = 10,
    cached: int = 0,
    written: int = 0,
) -> Any:
    return Response.model_validate(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 0.0,
            "model": model,
            "output": output,
            "status": status,
            "incomplete_details": {"reason": incomplete_reason} if incomplete_reason else None,
            "error": error,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
    )


def text(body: str) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": body, "annotations": []}],
    }


def refusal(body: str = "I can't help with that.") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "refusal", "refusal": body}],
    }


def reasoning() -> dict[str, Any]:
    return {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "enc"}


def tool_call(call_id: str, name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
    return {
        "id": f"fc_{call_id}",
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
        "status": "completed",
    }


class FakeSDK:
    """Stands in for AsyncOpenAI: `responses.create` pops the next scripted response (or raises
    it) and records the request as it was at call time."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.responses = self

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append({**kwargs, "input": list(kwargs["input"])})
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
                    "properties": {
                        "query": {"type": "string", "maxLength": 200},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
                    },
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


class ChatToolRunner(Runner):
    """Like the chat tools: every property is required, the optional ones nullable."""

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "propose_idea",
                "description": "Propose an idea.",
                "strict": True,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "est_cost": {"type": ["number", "null"]},
                    },
                    "required": ["title", "est_cost"],
                    "additionalProperties": False,
                },
            }
        ]


def client_for(sdk: FakeSDK, **settings: Any) -> OpenAIClient:
    cfg = make_settings(demo_mode=False, openai_api_key="test-key", **settings)
    return OpenAIClient(cfg, sdk=sdk)  # type: ignore[arg-type]


async def run(client: AIClient, tools: ToolRunner | None = None, tier: Any = "smart") -> Any:
    return await client.run(
        tier=tier,
        system="SYSTEM",
        context='{"ctx":1}',
        messages=[{"role": "user", "content": "plan"}],
        tools=tools,
        output_model=Pick,
    )


# --- OpenAIClient -------------------------------------------------------------------------


def test_a_key_is_required_unless_an_sdk_is_given() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        OpenAIClient(make_settings(demo_mode=False, openai_api_key=None))


async def test_single_turn_smart_request_shape_and_result() -> None:
    sdk = FakeSDK(reply([reasoning(), text(PICK_JSON)], cached=40, written=7))
    result = await run(client_for(sdk))
    assert result.output == Pick(title="Pottery", n=3)
    # input_tokens includes the cached and cache-written tokens: they are split out.
    assert result.usage == Usage(
        model="gpt-6.2-sol", input=53, output=10, cache_read=40, cache_write=7
    )
    assert result.tool_calls == 0 and result.degraded == set()

    [req] = sdk.requests
    assert req["model"] == "gpt-6.2-sol"
    assert req["instructions"] == "SYSTEM"
    assert req["max_output_tokens"] == 16_000
    assert req["store"] is False
    assert req["include"] == ["reasoning.encrypted_content"]
    assert req["reasoning"] == {"effort": "medium"}
    assert "tools" not in req
    # The couple's context is the first input item, so the stable part leads the prefix.
    assert req["input"] == [
        {"role": "developer", "content": '{"ctx":1}'},
        {"role": "user", "content": "plan"},
    ]
    fmt = req["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["name"] == "Pick" and fmt["strict"] is True
    assert fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == {"title", "n"}


async def test_fast_tier_uses_the_fast_model_and_effort() -> None:
    sdk = FakeSDK(reply([text(PICK_JSON)], model="gpt-6-luna"))
    result = await run(client_for(sdk), tier="fast")
    [req] = sdk.requests
    assert req["model"] == "gpt-6-luna"
    assert req["reasoning"] == {"effort": "low"}
    assert result.usage.model == "gpt-6-luna"


async def test_tool_loop_executes_tools_replays_the_output_and_sums_usage() -> None:
    first = reply(
        [
            reasoning(),
            text("Let me look."),
            tool_call("call_1", "search_places", {"query": "pottery", "max_results": None}),
            tool_call("call_2", "search_places", {"query": "cafe", "max_results": 3}),
        ],
        input_tokens=100,
        output_tokens=20,
    )
    second = reply([text(PICK_JSON)], input_tokens=530, output_tokens=15, cached=500)
    sdk = FakeSDK(first, second)
    runner = Runner({"search_places": '{"data":[{"place_id":"p1"}]}'})
    runner.degraded = {"events"}

    result = await run(client_for(sdk), runner)

    assert result.tool_calls == 2
    # An optional argument the model left null is dropped; one it filled in is kept.
    assert runner.calls == [
        ("search_places", {"query": "pottery"}),
        ("search_places", {"query": "cafe", "max_results": 3}),
    ]
    assert result.degraded == {"events"}
    assert result.usage == Usage(model="gpt-6.2-sol", input=130, output=35, cache_read=500)
    # The tools go in as strict function tools; nothing is stored server-side, so the next
    # request carries the whole previous output (reasoning too), then every tool result.
    assert sdk.requests[0]["tools"] == [_function_tool(runner.specs()[0])]
    replayed = sdk.requests[1]["input"][2:]
    assert replayed == [
        *(item.model_dump(exclude_none=True) for item in first.output),
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"data":[{"place_id":"p1"}]}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_2",
            "output": '{"data":[{"place_id":"p1"}]}',
        },
    ]
    assert replayed[0]["type"] == "reasoning" and replayed[0]["encrypted_content"] == "enc"


async def test_null_arguments_the_spec_requires_are_kept() -> None:
    sdk = FakeSDK(
        reply([tool_call("call_1", "propose_idea", {"title": "Picnic", "est_cost": None})]),
        reply([text(PICK_JSON)]),
    )
    runner = ChatToolRunner()
    await run(client_for(sdk), runner)
    assert runner.calls == [("propose_idea", {"title": "Picnic", "est_cost": None})]


async def test_tool_failure_is_reported_to_the_model_not_raised() -> None:
    sdk = FakeSDK(
        reply([tool_call("call_1", "search_places", {"query": "x"})]),
        reply([text(PICK_JSON)]),
    )
    runner = Runner()
    runner.fail = True
    result = await run(client_for(sdk), runner)
    assert result.output.title == "Pottery"
    assert sdk.requests[1]["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"error": "search_places failed"}',
    }


async def test_unparseable_tool_arguments_are_reported_to_the_model_not_raised() -> None:
    sdk = FakeSDK(
        reply([tool_call("call_1", "search_places", "{not json")]),
        reply([text(PICK_JSON)]),
    )
    runner = Runner()
    result = await run(client_for(sdk), runner)
    assert result.output.title == "Pottery" and runner.calls == []
    assert sdk.requests[1]["input"][-1]["output"] == '{"error": "search_places failed"}'


async def test_iteration_cap_raises_truncated_with_usage_so_far() -> None:
    looping = [reply([tool_call(f"call_{i}", "search_places", {"query": "x"})]) for i in range(3)]
    sdk = FakeSDK(*looping)
    runner = Runner()
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk, ai_max_tool_iterations=2), runner)
    assert caught.value.code == "truncated"
    # Two rounds of tools, then the third request had to be the answer and was not.
    assert len(sdk.requests) == 3 and len(runner.calls) == 2
    assert caught.value.tool_calls == 2
    assert caught.value.usage is not None and caught.value.usage.input == 300


async def test_a_tool_call_with_no_tools_is_unavailable() -> None:
    sdk = FakeSDK(reply([tool_call("call_1", "search_places", {"query": "x"})]))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "ai_unavailable"


async def test_refusal_content_maps_to_refused() -> None:
    sdk = FakeSDK(reply([refusal()], output_tokens=0))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "refused"
    assert caught.value.usage == Usage(model="gpt-6.2-sol", input=100)


async def test_content_filter_maps_to_refused() -> None:
    sdk = FakeSDK(reply([], status="incomplete", incomplete_reason="content_filter"))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "refused"


async def test_max_output_tokens_maps_to_truncated() -> None:
    sdk = FakeSDK(
        reply(
            [text('{"title":"Pott')],
            status="incomplete",
            incomplete_reason="max_output_tokens",
            output_tokens=16000,
        )
    )
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "truncated"
    assert caught.value.usage is not None and caught.value.usage.output == 16000


async def test_any_other_incomplete_reason_is_truncated_naming_it() -> None:
    sdk = FakeSDK(reply([], status="incomplete", incomplete_reason="steered"))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "truncated" and "steered" in str(caught.value)


async def test_failed_status_maps_to_ai_unavailable_without_leaking_the_error() -> None:
    sdk = FakeSDK(reply([], status="failed", error={"code": "server_error", "message": "oops"}))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "ai_unavailable" and "oops" not in str(caught.value)


async def test_an_unexpected_status_is_ai_unavailable() -> None:
    sdk = FakeSDK(reply([], status="cancelled"))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "ai_unavailable"


async def test_sdk_errors_map_to_ai_unavailable_keeping_usage() -> None:
    boom = openai.APIConnectionError(request=httpx.Request("POST", "https://x.test"))
    sdk = FakeSDK(reply([tool_call("call_1", "search_places", {"query": "x"})]), boom)
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk), Runner())
    assert caught.value.code == "ai_unavailable"
    assert caught.value.tool_calls == 1 and caught.value.usage is not None
    assert caught.value.usage.input == 100


async def test_output_that_fails_validation_is_invalid_output() -> None:
    sdk = FakeSDK(reply([text('{"title":"x"}')]))
    with pytest.raises(AIError) as caught:
        await run(client_for(sdk))
    assert caught.value.code == "invalid_output"


async def test_usage_without_a_breakdown_or_at_all_is_still_counted() -> None:
    plain = reply([text(PICK_JSON)], input_tokens=60, output_tokens=5)
    plain.usage.input_tokens_details = None
    none = reply([text(PICK_JSON)])
    none.usage = None
    assert (await run(client_for(FakeSDK(plain)))).usage == Usage(
        model="gpt-6.2-sol", input=60, output=5
    )
    assert (await run(client_for(FakeSDK(none)))).usage == Usage(model="gpt-6.2-sol")


def test_usage_add_keeps_every_priced_line_across_rounds() -> None:
    lined = Usage(
        "gpt-6.2-sol",
        input=2000,
        lines=(("gpt-6-luna", 1000, 0, 0, 0), ("gpt-6.2-sol", 1000, 0, 0, 0)),
    )
    plain = Usage("gpt-6.2-sol", input=5, output=1)
    total = Usage(model="gpt-6.2-sol") + lined + plain
    assert (total.model, total.input, total.output) == ("gpt-6.2-sol", 2005, 1)
    assert total.lines == (
        ("gpt-6-luna", 1000, 0, 0, 0),
        ("gpt-6.2-sol", 1000, 0, 0, 0),
        ("gpt-6.2-sol", 5, 1, 0, 0),
    )
    assert Usage("gpt-6.2-sol").priced_lines() == ()  # nothing used, nothing to price


# --- Strict schemas and tools -------------------------------------------------------------


def schema_nodes(node: Any) -> list[dict[str, Any]]:
    """Every dict in a schema, however deep."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(schema_nodes(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(schema_nodes(item))
    return found


@pytest.mark.parametrize("model", [PlanOptions, GeneratedIdeas])
def test_strict_schema_closes_every_object_and_drops_unsupported_keywords(
    model: type[BaseModel],
) -> None:
    schema = _strict_schema(model)
    nodes = schema_nodes(schema)
    objects = [n for n in nodes if isinstance(n.get("properties"), dict)]
    assert len(objects) > 1
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])
    for node in nodes:
        assert not {"minLength", "maxLength", "default"} & node.keys()
    # Still the model's own schema: Pydantic enforces the dropped limits afterwards.
    assert "$defs" in schema and schema["required"] == list(schema["properties"])


def test_strict_schema_notes_length_limits_and_keeps_item_limits() -> None:
    schema = _strict_schema(PlanOptions)
    option = schema["$defs"]["PlanOption"]
    assert option["properties"]["title"]["description"].endswith("(at most 120 characters)")
    assert option["properties"]["stops"]["maxItems"] == 5
    assert schema["properties"]["options"]["minItems"] == 3
    # An optional field that was already nullable stays as it was.
    assert {"type": "null"} in option["properties"]["est_cost"]["anyOf"]


def test_strict_schema_makes_optional_fields_required_without_changing_their_schema() -> None:
    class Opt(BaseModel):
        a: str
        b: str = "x"
        c: str | None = None
        d: list[str] = Field(default_factory=list)

    schema = _strict_schema(Opt)
    assert schema["required"] == ["a", "b", "c", "d"]
    # Not nullable unless the field already was: the model would reject a null.
    assert schema["properties"]["b"] == {"type": "string", "title": "B"}
    assert schema["properties"]["c"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert schema["properties"]["d"]["type"] == "array"
    assert "default" not in schema["properties"]["c"]
    # The original schema is not modified.
    assert Opt.model_json_schema()["required"] == ["a"]


def test_strict_schema_keeps_default_factory_lists_non_null_in_the_ideas_schema() -> None:
    idea = _strict_schema(GeneratedIdeas)["$defs"]["GeneratedIdea"]
    assert "place_ids" in idea["required"]
    place_ids = idea["properties"]["place_ids"]
    assert place_ids["type"] == "array" and "anyOf" not in place_ids
    assert {"type": "null"} in idea["properties"]["event_id"]["anyOf"]  # `X | None` stays nullable


def test_function_tool_makes_optional_arguments_required_and_nullable() -> None:
    spec = next(s for s in TOOL_SPECS if s["name"] == "search_places")
    tool = _function_tool(spec)
    assert tool["type"] == "function" and tool["strict"] is True
    assert tool["name"] == "search_places" and tool["description"] == spec["description"]
    params = tool["parameters"]
    assert params["required"] == ["query", "open_at", "max_results"]
    assert params["additionalProperties"] is False
    assert {"type": "null"} in params["properties"]["open_at"]["anyOf"]
    assert {"type": "null"} in params["properties"]["max_results"]["anyOf"]
    assert params["properties"]["query"]["description"].endswith("(at most 200 characters)")
    assert "maxLength" not in params["properties"]["query"]
    assert spec["input_schema"]["required"] == ["query"]  # the spec itself is untouched


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
    keyed = make_settings(demo_mode=True, openai_api_key="k")
    assert isinstance(get_ai_client(keyed), OpenAIClient)

    demo = get_ai_client(make_settings(demo_mode=True, openai_api_key=None))
    assert isinstance(demo, FakeAIClient)
    assert demo.responders is FAKE_RESPONDERS  # callers extend the shared map

    assert get_ai_client(make_settings(demo_mode=False, openai_api_key=None)) is None


def test_get_ai_dependency_is_503_when_unavailable() -> None:
    with pytest.raises(ApiError) as caught:
        get_ai(make_settings(demo_mode=False, openai_api_key=None))
    assert caught.value.status == 503 and caught.value.code == "ai_unavailable"
    assert isinstance(get_ai(make_settings(demo_mode=True, openai_api_key=None)), FakeAIClient)
