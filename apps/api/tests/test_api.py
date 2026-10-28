from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FailingModel, ScriptedModel


def client(model):
    return TestClient(create_app(model))


def test_session_flow_and_calendar():
    model = ScriptedModel(
        [[("get_clinic_info", {"topic": "parking"})]],
        ["There's valet parking for 25 reais."],
    )
    with client(model) as c:
        session = c.post("/api/sessions", json={"language": "en"}).json()
        assert "Sofia" in session["greeting"]

        reply = c.post(f"/api/sessions/{session['session_id']}/messages", json={"text": "Is there parking?"}).json()
        assert reply["reply"] == "There's valet parking for 25 reais."
        assert [e["type"] for e in reply["events"]] == ["tool_call", "tool_result", "done"]
        # The model saw the greeting as its own earlier turn
        assert model.calls[0][1] == {"role": "assistant", "content": session["greeting"]}

        # Startup seeded demo bookings for the next two weeks
        appointments = c.get("/api/appointments", params={"days": 14}).json()
        assert appointments and {"start", "end", "dentist_id"} <= set(appointments[0])
        clinic = c.get("/api/clinic").json()
        assert len(clinic["dentists"]) == 3
        assert clinic["hours"]["0"] == [["08:00", "12:00"], ["13:00", "18:00"]] and clinic["hours"]["6"] == []


def test_errors():
    with client(FailingModel()) as c:
        assert c.post("/api/sessions/nope/messages", json={"text": "hi"}).status_code == 404
        sid = c.post("/api/sessions").json()["session_id"]
        assert c.post(f"/api/sessions/{sid}/messages", json={"text": "  "}).status_code == 422
        assert c.post(f"/api/sessions/{sid}/messages", json={"text": "hi"}).status_code == 502
