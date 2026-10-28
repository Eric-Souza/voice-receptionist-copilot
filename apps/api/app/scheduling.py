"""Free-slot search and booking rules, shared by the tools and the seed script."""

from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.clinic import BOOKING_HORIZON_DAYS, DENTISTS, HOURS, MIN_NOTICE_MINUTES, SERVICES, SLOT_MINUTES
from app.models import Appointment

PARTS_OF_DAY = {"morning": (0, 12), "afternoon": (12, 24)}


class SchedulingError(ValueError):
    """A request the caller can fix (unknown service, slot taken, ...). The message goes back to the model."""


def dentists_for(service: str, dentist_id: str | None = None) -> list[str]:
    if service not in SERVICES:
        raise SchedulingError(f"Unknown service '{service}'. Valid: {', '.join(SERVICES)}.")
    if dentist_id:
        if dentist_id not in DENTISTS:
            raise SchedulingError(f"Unknown dentist '{dentist_id}'. Valid: {', '.join(DENTISTS)}.")
        if service not in DENTISTS[dentist_id].services:
            raise SchedulingError(f"{DENTISTS[dentist_id].name} doesn't do {SERVICES[service].name_en}.")
        return [dentist_id]
    return [d.id for d in DENTISTS.values() if service in d.services]


def _booked(session: Session, dentist_id: str, day: date, exclude_id: int | None) -> list[tuple[datetime, datetime]]:
    start = datetime.combine(day, datetime.min.time())
    rows = session.exec(
        select(Appointment).where(
            Appointment.dentist_id == dentist_id,
            Appointment.status == "booked",
            Appointment.start >= start,
            Appointment.start < start + timedelta(days=1),
        )
    ).all()
    return [(a.start, a.end) for a in rows if a.id != exclude_id]


def free_slots(
    session: Session,
    service: str,
    day: date,
    now: datetime,
    dentist_id: str | None = None,
    part_of_day: str | None = None,
    exclude_id: int | None = None,
) -> list[tuple[datetime, str]]:
    """(start, dentist_id) pairs where the whole service fits inside opening hours and nobody's booked.

    exclude_id ignores one appointment, so rescheduling can move it into its own neighborhood.
    """
    minutes = SERVICES[service].minutes
    earliest = now + timedelta(minutes=MIN_NOTICE_MINUTES)
    if day > now.date() + timedelta(days=BOOKING_HORIZON_DAYS):
        return []
    hours = PARTS_OF_DAY.get(part_of_day or "", (0, 24))
    slots = []
    for did in dentists_for(service, dentist_id):
        booked = _booked(session, did, day, exclude_id)
        for open_, close in HOURS[day.weekday()]:
            start = datetime.combine(day, open_)
            block_end = datetime.combine(day, close)
            while start + timedelta(minutes=minutes) <= block_end:
                end = start + timedelta(minutes=minutes)
                if (
                    start >= earliest
                    and hours[0] <= start.hour < hours[1]
                    and not any(start < b_end and b_start < end for b_start, b_end in booked)
                ):
                    slots.append((start, did))
                start += timedelta(minutes=SLOT_MINUTES)
    return sorted(slots)


def check_bookable(
    session: Session,
    *,
    patient_name: str,
    patient_phone: str,
    service: str,
    start: datetime,
    dentist_id: str,
    now: datetime,
) -> str:
    """Raise SchedulingError if this booking can't be made; return the normalized phone."""
    phone = normalize_phone(patient_phone)
    if not patient_name.strip():
        raise SchedulingError("The patient's name is required.")
    if (start, dentist_id) not in free_slots(session, service, start.date(), now, dentist_id):
        raise SchedulingError("That time isn't available. Check availability again and offer another time.")
    return phone


def book(
    session: Session,
    *,
    patient_name: str,
    patient_phone: str,
    service: str,
    start: datetime,
    dentist_id: str,
    now: datetime,
    notes: str = "",
) -> Appointment:
    phone = check_bookable(
        session,
        patient_name=patient_name,
        patient_phone=patient_phone,
        service=service,
        start=start,
        dentist_id=dentist_id,
        now=now,
    )
    appt = Appointment(
        patient_name=patient_name.strip(),
        patient_phone=phone,
        dentist_id=dentist_id,
        service=service,
        start=start,
        end=start + timedelta(minutes=SERVICES[service].minutes),
        notes=notes.strip(),
    )
    session.add(appt)
    session.commit()
    return appt


def normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]  # drop Brazil's country code
    if not 8 <= len(digits) <= 15:
        raise SchedulingError("That phone number doesn't look right. Ask the caller to repeat it.")
    return digits
