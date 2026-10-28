import json

import httpx
from openai import AsyncOpenAI

from app.llm import OpenAIChat, ToolCall


def sse(*deltas):
    events = [
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": d, "finish_reason": None}],
        }
        for d in deltas
    ]
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def client_for(handler):
    transport = httpx.MockTransport(handler)
    return AsyncOpenAI(
        api_key="x", base_url="http://llm.test/v1", max_retries=0, http_client=httpx.AsyncClient(transport=transport)
    )


async def collect(model, *args):
    return [c async for c in model.stream([{"role": "user", "content": "hi"}], [])]


async def test_streams_text_and_assembles_tool_call_fragments():
    body = sse(
        {"content": "One "},
        {"content": "moment."},
        {"tool_calls": [{"index": 0, "id": "t1", "function": {"name": "get_clinic_info", "arguments": '{"to'}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": 'pic": "parking"}'}}]},
    )
    model = OpenAIChat(client_for(lambda r: httpx.Response(200, text=body)), "main")
    chunks = await collect(model)
    assert chunks == ["One ", "moment.", [ToolCall("t1", "get_clinic_info", '{"topic": "parking"}')]]


async def test_falls_back_to_the_next_model_when_rate_limited():
    asked = []

    def handler(request):
        model = json.loads(request.content)["model"]
        asked.append(model)
        if model == "main":
            return httpx.Response(429, json={"error": {"message": "tokens per day", "type": "tokens"}})
        return httpx.Response(200, text=sse({"content": "Oi!"}))

    chunks = await collect(OpenAIChat(client_for(handler), "main", fallbacks=["backup"]))
    assert chunks == ["Oi!"] and asked == ["main", "backup"]
