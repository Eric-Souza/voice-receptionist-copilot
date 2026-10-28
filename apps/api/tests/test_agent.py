import asyncio
import json
import time

import pytest

from app.agent import Conversation, Receptionist
from tests.conftest import NOW
from tests.fakes import FailingModel, FlakyModel, ScriptedModel


async def run(receptionist, conversation, text):
    return [e async for e in receptionist.respond(conversation, text)]


def make(model):
    return Receptionist(model, now=lambda: NOW)


async def test_plain_reply_streams_text_and_records_history():
    model = ScriptedModel(["Claro! ", "Qual serviço?"])
    conversation = Conversation()
    events = await run(make(model), conversation, "Quero marcar uma consulta")
    assert [e["delta"] for e in events if e["type"] == "text"] == ["Claro! ", "Qual serviço?"]
    assert events[-1] == {"type": "done", "text": "Claro! Qual serviço?"}
    assert conversation.messages == [
        {"role": "user", "content": "Quero marcar uma consulta"},
        {"role": "assistant", "content": "Claro! Qual serviço?"},
    ]
    system = model.calls[0][0]
    assert system["role"] == "system" and "Wednesday, 2026-10-28 09:10" in system["content"]


async def test_tool_round_trip_feeds_results_back_to_the_model():
    model = ScriptedModel(
        ["Let me check. ", [("check_availability", {"service": "checkup", "date": "2026-10-29"})]],
        ["Thursday at 8 works. Shall I book it?"],
    )
    conversation = Conversation()
    events = await run(make(model), conversation, "A checkup tomorrow?")

    kinds = [e["type"] for e in events if e["type"] != "text"]
    assert kinds == ["tool_call", "tool_result", "done"]
    result = next(e for e in events if e["type"] == "tool_result")["result"]
    assert result["slots"][0]["start"] == "2026-10-29T08:00"
    assert events[-1]["text"] == "Let me check. Thursday at 8 works. Shall I book it?"

    # Second model call sees the assistant tool call and the tool's JSON result
    second = model.calls[1]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "check_availability"
    assert second[-1]["role"] == "tool" and json.loads(second[-1]["content"]) == result


BOOKING = {
    "patient_name": "Ana Paula",
    "patient_phone": "11987654321",
    "service": "checkup",
    "start": "2026-10-29T08:00",
    "dentist_id": "ana",
}


def results(events, name):
    return [e["result"] for e in events if e["type"] == "tool_result" and e["name"] == name]


async def test_booking_is_saved_only_after_the_caller_says_yes():
    model = ScriptedModel(
        # The model jumps the gun and confirms in the same turn; the tool refuses
        [[("book_appointment", BOOKING)]],
        [[("confirm_action", {"action_id": "A1"})]],
        ["Thursday at 8 am with Dra. Ana, is that right?"],
        [[("confirm_action", {"action_id": "A1"})]],
        ["You're booked."],
        [[("find_appointments", {"patient_phone": "11987654321"})]],
        ["You have a check-up Thursday at 8 am."],
    )
    receptionist, conversation = make(model), Conversation(language="en")
    events = await run(receptionist, conversation, "Ana Paula, 11 98765-4321, the 8 am please")
    assert "hasn't answered" in results(events, "confirm_action")[0]["error"]
    assert "Read this back" in results(events, "book_appointment")[0]["next"]

    events = await run(receptionist, conversation, "Yes")
    assert "booked" in results(events, "confirm_action")[0]

    events = await run(receptionist, conversation, "What do I have booked?")
    found = results(events, "find_appointments")[0]["appointments"]
    assert [a["start"] for a in found] == ["2026-10-29T08:00"]
    assert "The call started in English" in model.calls[0][0]["content"]


async def test_tool_errors_go_back_to_the_model_not_the_caller():
    model = ScriptedModel(
        [[("check_availability", {"service": "botox", "date": "2026-10-29"})]],
        ["Sorry, we don't offer that."],
    )
    events = await run(make(model), Conversation(), "Botox?")
    assert "error" in next(e for e in events if e["type"] == "tool_result")["result"]
    assert events[-1]["text"] == "Sorry, we don't offer that."


async def test_max_rounds_stops_a_tool_loop():
    loop = [[("get_clinic_info", {"topic": "parking"})]]
    model = ScriptedModel(loop, loop, loop)
    events = await run(Receptionist(model, now=lambda: NOW, max_rounds=2), Conversation(), "Parking?")
    assert len(model.calls) == 2
    assert events[-1] == {"type": "done", "text": ""}


async def test_model_failure_drops_the_turn():
    conversation = Conversation([{"role": "assistant", "content": "Hi"}])
    events = await run(make(FailingModel()), conversation, "Hello")
    assert [e["type"] for e in events] == ["error", "done"]
    assert conversation.messages == [{"role": "assistant", "content": "Hi"}]


async def test_an_empty_reply_is_retried_once():
    model = ScriptedModel([], ["Claro, posso ajudar."])
    conversation = Conversation()
    events = await run(make(model), conversation, "Oi")
    assert len(model.calls) == 2
    assert events[-1] == {"type": "done", "text": "Claro, posso ajudar."}
    assert conversation.messages[-1] == {"role": "assistant", "content": "Claro, posso ajudar."}


async def test_a_failed_model_call_is_retried_once_before_any_output():
    events = await run(make(FlakyModel(["Oi!"])), Conversation(), "Oi")
    assert events[-1] == {"type": "done", "text": "Oi!"}


PROPOSAL = [[("book_appointment", BOOKING)]], ["Então é um checkup quinta às 8h com a Dra. Ana, certo?"]


async def test_interrupting_a_proposal_withdraws_it():
    conversation = Conversation()
    await run(make(ScriptedModel(*PROPOSAL)), conversation, "Ana Paula, 11 98765-4321")
    assert conversation.pending

    # The caller talked over the read-back, so they never agreed to it
    assert conversation.interrupt("Então é um checkup quinta às 8h,") is False
    assert not conversation.pending
    assert [m["role"] for m in conversation.messages] == ["user", "assistant", "tool", "assistant"]
    assert conversation.messages[1]["tool_calls"] and conversation.messages[1]["content"] is None
    assert conversation.messages[-1] == {"role": "assistant", "content": "Então é um checkup quinta às 8h,…"}


async def test_interrupting_before_anything_was_said_drops_the_turn():
    conversation = Conversation([{"role": "assistant", "content": "Como posso ajudar?"}])
    await run(make(ScriptedModel(["Claro! Qual serviço?"])), conversation, "Quero marcar")
    assert conversation.interrupt("") is True
    assert conversation.messages == [{"role": "assistant", "content": "Como posso ajudar?"}]
    assert conversation.turns == 0


async def test_cancelling_during_a_tool_still_records_its_result():
    class SlowTools(Receptionist):
        def _run_tool(self, *args):
            time.sleep(0.2)
            return super()._run_tool(*args)

    receptionist = SlowTools(ScriptedModel(*PROPOSAL), now=lambda: NOW)
    conversation = Conversation()
    tool_started = asyncio.Event()

    async def listen():
        async for event in receptionist.respond(conversation, "Ana Paula, 11 98765-4321"):
            if event["type"] == "tool_call":
                tool_started.set()

    turn = asyncio.create_task(listen())
    await tool_started.wait()
    await asyncio.sleep(0.05)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    # The tool ran to the end and its result is in the history, matching what it did
    assert conversation.messages[-1]["role"] == "tool"
    assert conversation.pending
    assert conversation.interrupt("") is False
    assert conversation.messages[-1] == {"role": "assistant", "content": "…"}
