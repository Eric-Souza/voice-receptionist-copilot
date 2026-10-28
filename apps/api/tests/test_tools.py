import json

from app.tools import TOOLS, clinic_info, run_tool, tool_schemas

PHONE = "512-555-0199"


CHANGE_TOOLS = {"book_appointment", "reschedule_appointment", "cancel_appointment"}


def propose(ctx, name, **args):
    return run_tool(ctx, name, json.dumps(args))


def call(ctx, name, **args):
    """Run a tool; for the change tools, also play the caller saying yes on the next turn."""
    result = propose(ctx, name, **args)
    if name in CHANGE_TOOLS and "pending" in result:
        ctx.turn += 1
        result = propose(ctx, "confirm_action", action_id=result["pending"]["action_id"])
    return result


def book_thursday(ctx, time="09:00", service="checkup", dentist="anna"):
    return call(
        ctx,
        "book_appointment",
        patient_name="Jane Doe",
        patient_phone=PHONE,
        service=service,
        start=f"2026-10-29T{time}",
        dentist_id=dentist,
    )


def test_schemas_are_valid_function_definitions():
    schemas = tool_schemas()
    assert {s["function"]["name"] for s in schemas} == set(TOOLS)
    for s in schemas:
        params = s["function"]["parameters"]
        assert set(params["required"]) <= set(params["properties"])
    topic = TOOLS["get_clinic_info"].parameters["properties"]["topic"]
    assert "insurance" in topic["enum"]


def test_check_availability_groups_dentists_and_caps_results(ctx):
    result = call(ctx, "check_availability", service="checkup", date="2026-10-29")
    assert len(result["slots"]) == 6
    first = result["slots"][0]
    assert first["start"] == "2026-10-29T08:00"
    assert first["say"] == "Thursday the 29th at 8 am"
    assert {d["id"] for d in first["dentists"]} == {"anna", "brian", "carla"}


def test_check_availability_searches_ahead_over_closed_days(ctx):
    result = call(ctx, "check_availability", service="root_canal", date="2026-11-01", days=3, part_of_day="afternoon")
    # Sunday is closed, so the first afternoon slot is Monday's 13:00
    assert result["slots"][0]["start"] == "2026-11-02T13:00"


def test_check_availability_reports_empty_ranges(ctx):
    result = call(ctx, "check_availability", service="checkup", date="2026-11-01")
    assert result["slots"] == [] and "note" in result


def test_check_availability_says_where_a_cut_off_list_ends(ctx):
    result = call(ctx, "check_availability", service="checkup", date="2026-10-29", days=7)
    assert len(result["slots"]) == 6 and {s["start"][:10] for s in result["slots"]} == {"2026-10-29"}
    assert "up to Thursday the 29th" in result["note"]


def test_null_optional_arguments_are_left_out(ctx):
    handoff = call(ctx, "handoff_to_human", reason="Wants a quote", patient_phone=PHONE, patient_name=None)
    assert handoff["handoff_id"]
    args = dict(patient_phone=PHONE, service="checkup", start="2026-10-29T09:00", dentist_id="anna")
    assert "patient_name" in propose(ctx, "book_appointment", patient_name=None, **args)["error"]
    assert "booked" in call(ctx, "book_appointment", patient_name="Jane Doe", notes=None, **args)


def test_bad_input_comes_back_as_an_error(ctx):
    assert "error" in call(ctx, "check_availability", service="botox", date="2026-10-29")
    assert "error" in call(ctx, "check_availability", service="checkup", date="next thursday")
    assert "error" in call(ctx, "check_availability", service="checkup", date="2026-10-01")
    assert "error" in call(ctx, "check_availability", service="checkup")  # missing date
    assert "error" in run_tool(ctx, "check_availability", "{not json")
    assert "error" in run_tool(ctx, "make_coffee", "{}")
    assert "error" in call(ctx, "get_clinic_info", topic=42)


def test_book_find_reschedule_cancel(ctx):
    booked = book_thursday(ctx, service="cleaning")["booked"]
    assert booked["dentist"] == "Dr. Anna Reed" and booked["start"] == "2026-10-29T09:00"

    found = call(ctx, "find_appointments", patient_phone="(512) 555-0199")["appointments"]
    assert [a["appointment_id"] for a in found] == [booked["appointment_id"]]

    moved = call(
        ctx,
        "reschedule_appointment",
        appointment_id=booked["appointment_id"],
        patient_phone=PHONE,
        new_start="2026-10-29T09:30",
    )
    # The 45-minute cleaning runs 09:00-09:45, so moving it to 09:30 only works if it doesn't block itself
    assert moved["rescheduled"]["start"] == "2026-10-29T09:30"
    assert moved["previous_start"] == "2026-10-29T09:00"

    cancelled = call(ctx, "cancel_appointment", appointment_id=booked["appointment_id"], patient_phone=PHONE)
    assert cancelled["cancelled"]["appointment_id"] == booked["appointment_id"]
    assert call(ctx, "find_appointments", patient_phone=PHONE)["appointments"] == []


def test_reschedule_can_switch_dentist_but_not_to_one_without_the_service(ctx):
    appt_id = book_thursday(ctx, service="cleaning")["booked"]["appointment_id"]
    bad = call(
        ctx,
        "reschedule_appointment",
        appointment_id=appt_id,
        patient_phone=PHONE,
        new_start="2026-10-29T10:00",
        dentist_id="brian",
    )
    assert "doesn't do" in bad["error"]
    ok = call(
        ctx,
        "reschedule_appointment",
        appointment_id=appt_id,
        patient_phone=PHONE,
        new_start="2026-10-29T10:00",
        dentist_id="carla",
    )
    assert ok["rescheduled"]["dentist"] == "Dr. Carla Mendez"


def test_reschedule_switches_dentist_when_the_current_one_is_busy(ctx):
    appt_id = book_thursday(ctx, service="cleaning")["booked"]["appointment_id"]
    book_thursday(ctx, time="10:00", service="cleaning")  # Dr. Reed is now busy at 10:00
    args = dict(appointment_id=appt_id, patient_phone=PHONE, new_start="2026-10-29T10:00")
    moved = call(ctx, "reschedule_appointment", **args)
    assert moved["rescheduled"]["dentist"] == "Dr. Carla Mendez"


def test_changes_wait_for_the_callers_next_turn(ctx):
    proposal = propose(
        ctx,
        "book_appointment",
        patient_name="Jane Doe",
        patient_phone=PHONE,
        service="checkup",
        start="2026-10-29T09:00",
        dentist_id="anna",
    )
    pending = proposal["pending"]
    assert pending["say"] == "Thursday the 29th at 9 am" and pending["patient_phone"] == "5125550199"
    assert call(ctx, "find_appointments", patient_phone=PHONE)["appointments"] == []

    # Confirming in the same turn it was proposed is refused: the caller hasn't heard the read-back yet
    same_turn = propose(ctx, "confirm_action", action_id=pending["action_id"])
    assert "hasn't answered" in same_turn["error"]

    ctx.turn += 1
    assert "booked" in propose(ctx, "confirm_action", action_id=pending["action_id"])
    # An action runs once
    assert "error" in propose(ctx, "confirm_action", action_id=pending["action_id"])
    assert "error" in propose(ctx, "confirm_action", action_id="A99")


def test_proposals_are_validated_up_front_and_again_on_confirm(ctx):
    args = dict(patient_name="Jane Doe", service="checkup", start="2026-10-29T09:00", dentist_id="anna")
    assert "phone" in propose(ctx, "book_appointment", patient_phone="123", **args)["error"]
    first = propose(ctx, "book_appointment", patient_phone=PHONE, **args)["pending"]
    second = propose(ctx, "book_appointment", patient_phone="5125550198", **args)["pending"]
    ctx.turn += 1
    assert "booked" in propose(ctx, "confirm_action", action_id=second["action_id"])
    # The slot went to the other caller in the meantime
    assert "isn't available" in propose(ctx, "confirm_action", action_id=first["action_id"])["error"]


def test_cancel_is_only_a_proposal_until_confirmed(ctx):
    appt_id = book_thursday(ctx)["booked"]["appointment_id"]
    proposal = propose(ctx, "cancel_appointment", appointment_id=appt_id, patient_phone=PHONE)
    assert proposal["pending"]["appointment"]["appointment_id"] == appt_id
    assert len(call(ctx, "find_appointments", patient_phone=PHONE)["appointments"]) == 1


def test_other_callers_cant_touch_an_appointment(ctx):
    appt_id = book_thursday(ctx)["booked"]["appointment_id"]
    result = call(ctx, "cancel_appointment", appointment_id=appt_id, patient_phone="5125550198")
    assert "error" in result


def test_late_cancellation_adds_a_policy_note(ctx):
    appt_id = book_thursday(ctx, time="08:30")["booked"]["appointment_id"]  # under 24h from Wednesday 09:10
    assert "24 hours" in call(ctx, "cancel_appointment", appointment_id=appt_id, patient_phone=PHONE)["note"]


def test_spoken_times():
    from datetime import datetime

    from app.tools import _spoken

    monday = _spoken(datetime(2026, 11, 2, 14, 30))
    assert monday == "Monday the 2nd at 2:30 pm"
    assert _spoken(datetime(2026, 11, 11, 12, 0)) == "Wednesday the 11th at 12 pm"


def test_clinic_info_and_handoff(ctx):
    assert "Delta Dental" in call(ctx, "get_clinic_info", topic="insurance")["info"]
    assert "error" in call(ctx, "get_clinic_info", topic="weather")
    assert set(clinic_info()) >= {"location", "parking", "emergencies", "contact"}
    handoff = call(ctx, "handoff_to_human", reason="Wants a quote for implants", patient_phone=PHONE)
    assert handoff["handoff_id"]
