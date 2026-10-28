from datetime import date, datetime

import pytest

from app.scheduling import SchedulingError, book, free_slots, normalize_phone
from tests.conftest import NOW

THURSDAY = date(2026, 10, 29)
SATURDAY = date(2026, 10, 31)
SUNDAY = date(2026, 11, 1)


def starts(slots, dentist="anna"):
    return [s.strftime("%H:%M") for s, d in slots if d == dentist]


def test_weekday_slots_skip_lunch_and_fit_the_service(session):
    slots = free_slots(session, "whitening", THURSDAY, NOW, "anna")
    # 60 minutes: the last morning start is 11:00 and the last afternoon start is 17:00
    assert starts(slots)[0] == "08:00"
    assert "11:00" in starts(slots) and "11:30" not in starts(slots)
    assert "12:00" not in starts(slots) and "12:30" not in starts(slots)
    assert starts(slots)[-1] == "17:00"


def test_saturday_is_mornings_only_and_sunday_closed(session):
    assert starts(free_slots(session, "checkup", SATURDAY, NOW, "anna"))[-1] == "11:30"
    assert free_slots(session, "checkup", SUNDAY, NOW) == []


def test_today_needs_an_hour_of_notice(session):
    # Now is 09:10, so 10:10 is the earliest; the grid's next start is 10:30
    assert starts(free_slots(session, "checkup", NOW.date(), NOW, "anna"))[0] == "10:30"


def test_only_dentists_who_do_the_service(session):
    dentists = {d for _, d in free_slots(session, "root_canal", THURSDAY, NOW)}
    assert dentists == {"brian"}
    with pytest.raises(SchedulingError, match="doesn't do"):
        free_slots(session, "root_canal", THURSDAY, NOW, "anna")


def test_booking_blocks_overlapping_slots(session):
    book(
        session,
        patient_name="Ana Paula",
        patient_phone="(512) 555-0199",
        service="filling",
        start=datetime(2026, 10, 29, 9, 0),
        dentist_id="anna",
        now=NOW,
    )
    taken = starts(free_slots(session, "cleaning", THURSDAY, NOW, "anna"))
    # The filling runs 09:00-10:00; a 45-minute cleaning can't start 08:30, 09:00 or 09:30
    assert "08:00" in taken and "10:00" in taken
    assert not {"08:30", "09:00", "09:30"} & set(taken)


def test_double_booking_is_refused(session):
    args = dict(patient_phone="5125550198", service="checkup", dentist_id="carla", now=NOW)
    book(session, patient_name="First", start=datetime(2026, 10, 29, 14, 0), **args)
    with pytest.raises(SchedulingError, match="isn't available"):
        book(session, patient_name="Second", start=datetime(2026, 10, 29, 14, 0), **args)


def test_off_grid_and_closed_times_are_refused(session):
    args = dict(patient_name="X", patient_phone="5125550198", service="checkup", dentist_id="anna", now=NOW)
    for start in (datetime(2026, 10, 29, 9, 15), datetime(2026, 10, 29, 12, 0), datetime(2026, 11, 1, 9, 0)):
        with pytest.raises(SchedulingError):
            book(session, start=start, **args)


@pytest.mark.parametrize(
    ("raw", "digits"),
    [("(512) 555-0199", "5125550199"), ("+1 512 555 0199", "5125550199"), ("512.555.0199", "5125550199")],
)
def test_normalize_phone(raw, digits):
    assert normalize_phone(raw) == digits


def test_normalize_phone_rejects_short_numbers():
    with pytest.raises(SchedulingError):
        normalize_phone("1234")
