"""The receptionist's tools: OpenAI function schemas plus the Python that runs them.

Every tool returns a JSON-serializable dict. Mistakes the caller or model can fix come back as
{"error": "..."} so the model can apologize and try again instead of the turn failing.

Booking, rescheduling and cancelling only propose the change. It runs when the model calls confirm_action
on a later turn, i.e. after the caller has heard the read-back and answered. Models don't reliably wait for
a yes when told to, so this is enforced here rather than in the prompt.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.clinic import DENTISTS, SERVICES
from app.config import REPO_ROOT, get_settings
from app.models import Appointment, Handoff
from app.scheduling import (
    PARTS_OF_DAY,
    SchedulingError,
    book,
    check_bookable,
    dentists_for,
    free_slots,
    normalize_phone,
)

CLINIC_INFO_PATH = REPO_ROOT / "data" / "clinic-info.md"
# Distinct start times check_availability returns; the prompt asks the model to offer two or three
MAX_SLOTS = 6
MAX_SEARCH_DAYS = 14


@dataclass
class Pending:
    run: Callable[["ToolContext"], dict[str, Any]]
    turn: int


@dataclass
class ToolContext:
    session: Session
    now: datetime  # naive, clinic time
    turn: int = 0  # number of the caller's message in the conversation
    pending: dict[str, Pending] = field(default_factory=dict)  # proposed changes, kept across turns


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., dict[str, Any]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict[str, Any], required: list[str]):
    def register(fn: Callable[..., dict[str, Any]]):
        params = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
        TOOLS[name] = Tool(name, description, params, fn)
        return fn

    return register


def tool_schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in TOOLS.values()]


def run_tool(ctx: ToolContext, name: str, arguments: str) -> dict[str, Any]:
    if name not in TOOLS:
        return {"error": f"There's no tool called {name}."}
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return {"error": "The tool arguments weren't valid JSON."}
    try:
        return TOOLS[name].run(ctx, **args)
    except SchedulingError as exc:
        return {"error": str(exc)}
    except TypeError as exc:  # missing or unexpected arguments
        return {"error": f"Bad arguments for {name}: {exc}"}


# --- helpers ---------------------------------------------------------------------------------


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SchedulingError(f"'{value}' isn't a date in YYYY-MM-DD format.") from None


def _parse_start(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise SchedulingError(f"'{value}' isn't a time in YYYY-MM-DDTHH:MM format.") from None
    if dt.tzinfo:
        dt = dt.astimezone(ZoneInfo(get_settings().clinic_timezone)).replace(tzinfo=None)
    return dt.replace(second=0, microsecond=0)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


_WEEKDAYS_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _spoken(dt: datetime) -> dict[str, str]:
    """How to say a time aloud, so the model doesn't have to convert ISO times (it tends to say "13h" in English)."""
    hour12 = dt.hour % 12 or 12
    clock_en = f"{hour12}{dt.minute and f':{dt.minute:02d}' or ''} {'am' if dt.hour < 12 else 'pm'}"
    clock_pt = f"{dt.hour}h{dt.minute and f'{dt.minute:02d}' or ''}"
    return {
        "en": f"{dt:%A} the {_ordinal(dt.day)} at {clock_en}",
        "pt": f"{_WEEKDAYS_PT[dt.weekday()]}, dia {dt.day}, às {clock_pt}",
    }


def _describe(appt: Appointment) -> dict[str, Any]:
    service = SERVICES[appt.service]
    return {
        "appointment_id": appt.id,
        "patient_name": appt.patient_name,
        "service": appt.service,
        "service_name": service.name_en,
        "dentist": DENTISTS[appt.dentist_id].name,
        "start": _fmt(appt.start),
        "say": _spoken(appt.start),
        "minutes": service.minutes,
    }


def _propose(ctx: ToolContext, details: dict[str, Any], run: Callable[[ToolContext], dict[str, Any]]) -> dict:
    action_id = f"A{len(ctx.pending) + 1}"
    while action_id in ctx.pending:
        action_id += "x"
    ctx.pending[action_id] = Pending(run, ctx.turn)
    return {
        "pending": {"action_id": action_id, **details},
        "next": "Nothing is saved yet. Read this back to the caller as a question and end your turn. "
        "Call confirm_action with this action_id only after they say yes.",
    }


def _owned_appointment(ctx: ToolContext, appointment_id: int, patient_phone: str) -> Appointment:
    appt = ctx.session.get(Appointment, appointment_id)
    if not appt or appt.status != "booked" or appt.patient_phone != normalize_phone(patient_phone):
        raise SchedulingError("No booked appointment with that id for this phone number. Use find_appointments.")
    if appt.start <= ctx.now:
        raise SchedulingError("That appointment has already started or passed.")
    return appt


# --- tools -----------------------------------------------------------------------------------

_SERVICE = {"type": "string", "enum": list(SERVICES), "description": "Service key"}
_DENTIST = {"type": "string", "enum": list(DENTISTS), "description": "Dentist id; omit for any dentist"}
_PHONE = {"type": "string", "description": "Caller's phone number, digits with area code (DDD)"}
_APPT_ID = {"type": "integer", "description": "appointment_id from find_appointments"}


@tool(
    "check_availability",
    "Find the earliest free times for a service, starting on `date` and looking up to `days` days ahead. "
    "Only the earliest few times come back, so when the caller asks about a specific day, set date to that day.",
    {
        "service": _SERVICE,
        "date": {"type": "string", "description": "First day to search, YYYY-MM-DD"},
        "days": {"type": "integer", "description": f"Days to search, 1-{MAX_SEARCH_DAYS} (default 1)"},
        "dentist_id": _DENTIST,
        "part_of_day": {"type": "string", "enum": list(PARTS_OF_DAY)},
    },
    ["service", "date"],
)
def check_availability(
    ctx: ToolContext,
    service: str,
    date: str,
    days: int = 1,
    dentist_id: str | None = None,
    part_of_day: str | None = None,
) -> dict[str, Any]:
    first = _parse_date(date)
    if first < ctx.now.date():
        raise SchedulingError("That date is in the past.")
    dentists_for(service, dentist_id)  # validates both before searching
    by_start: dict[datetime, list[str]] = {}
    for offset in range(max(1, min(days, MAX_SEARCH_DAYS))):
        day = first + timedelta(days=offset)
        for start, did in free_slots(ctx.session, service, day, ctx.now, dentist_id, part_of_day):
            if len(by_start) == MAX_SLOTS and start not in by_start:
                break
            by_start.setdefault(start, []).append(did)
        if len(by_start) == MAX_SLOTS:
            break
    slots = [
        {
            "start": _fmt(start),
            "say": _spoken(start),
            "dentists": [{"id": did, "name": DENTISTS[did].name} for did in dids],
        }
        for start, dids in by_start.items()
    ]
    result: dict[str, Any] = {"service": service, "minutes": SERVICES[service].minutes, "slots": slots}
    if not slots:
        result["note"] = "Nothing free in that range. Try more days, another part of the day, or any dentist."
    elif len(slots) == MAX_SLOTS:
        result["note"] = f"Only the earliest {MAX_SLOTS} times; later times may be free too. Search a later date."
    return result


@tool(
    "book_appointment",
    "Propose a booking once you have the service, time, dentist, name and phone. Nothing is saved until the "
    "caller says yes and you call confirm_action.",
    {
        "patient_name": {"type": "string"},
        "patient_phone": _PHONE,
        "service": _SERVICE,
        "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM, from check_availability"},
        "dentist_id": {"type": "string", "enum": list(DENTISTS)},
        "notes": {"type": "string", "description": "Anything staff should know (pain, first visit, ...)"},
    },
    ["patient_name", "patient_phone", "service", "start", "dentist_id"],
)
def book_appointment(
    ctx: ToolContext,
    patient_name: str,
    patient_phone: str,
    service: str,
    start: str,
    dentist_id: str,
    notes: str = "",
) -> dict[str, Any]:
    dentists_for(service, dentist_id)
    booking = {
        "patient_name": patient_name,
        "patient_phone": patient_phone,
        "service": service,
        "start": _parse_start(start),
        "dentist_id": dentist_id,
    }
    phone = check_bookable(ctx.session, now=ctx.now, **booking)

    def run(ctx: ToolContext) -> dict[str, Any]:
        return {"booked": _describe(book(ctx.session, now=ctx.now, notes=notes, **booking))}

    details = {
        "action": "book",
        "patient_name": patient_name.strip(),
        "patient_phone": phone,
        "service_name": SERVICES[service].name_en,
        "dentist": DENTISTS[dentist_id].name,
        "say": _spoken(booking["start"]),
    }
    return _propose(ctx, details, run)


@tool(
    "find_appointments",
    "List the caller's upcoming appointments by phone number.",
    {"patient_phone": _PHONE},
    ["patient_phone"],
)
def find_appointments(ctx: ToolContext, patient_phone: str) -> dict[str, Any]:
    rows = ctx.session.exec(
        select(Appointment)
        .where(
            Appointment.patient_phone == normalize_phone(patient_phone),
            Appointment.status == "booked",
            Appointment.start > ctx.now,
        )
        .order_by(Appointment.start)
    ).all()
    return {"appointments": [_describe(a) for a in rows]}


@tool(
    "reschedule_appointment",
    "Propose moving an appointment to a new free time (check_availability first). Keeps the dentist if "
    "they're free, otherwise takes whoever is; dentist_id forces one. Nothing changes until the caller says yes "
    "and you call confirm_action.",
    {
        "appointment_id": _APPT_ID,
        "patient_phone": _PHONE,
        "new_start": {"type": "string", "description": "YYYY-MM-DDTHH:MM"},
        "dentist_id": _DENTIST,
    },
    ["appointment_id", "patient_phone", "new_start"],
)
def reschedule_appointment(
    ctx: ToolContext,
    appointment_id: int,
    patient_phone: str,
    new_start: str,
    dentist_id: str | None = None,
) -> dict[str, Any]:
    start = _parse_start(new_start)

    def checked(ctx: ToolContext) -> tuple[Appointment, str]:
        appt = _owned_appointment(ctx, appointment_id, patient_phone)
        dentists_for(appt.service, dentist_id)
        slots = free_slots(ctx.session, appt.service, start.date(), ctx.now, dentist_id, exclude_id=appt.id)
        free = {did for slot, did in slots if slot == start}
        if not free:
            raise SchedulingError("That time isn't available. Check availability again and offer another time.")
        # check_availability offers a time with whoever is free, so keep the dentist only if they are
        return appt, appt.dentist_id if appt.dentist_id in free else min(free)

    def run(ctx: ToolContext) -> dict[str, Any]:
        appt, did = checked(ctx)
        previous = _describe(appt)
        appt.start, appt.end = start, start + (appt.end - appt.start)
        appt.dentist_id = did
        ctx.session.add(appt)
        ctx.session.commit()
        return {"rescheduled": _describe(appt), "previous_start": previous["start"]}

    appt, did = checked(ctx)
    details = {
        "action": "reschedule",
        "appointment": _describe(appt),
        "new_dentist": DENTISTS[did].name,
        "new_say": _spoken(start),
    }
    return _propose(ctx, details, run)


@tool(
    "cancel_appointment",
    "Propose cancelling an appointment. Nothing changes until the caller says yes and you call confirm_action.",
    {"appointment_id": _APPT_ID, "patient_phone": _PHONE},
    ["appointment_id", "patient_phone"],
)
def cancel_appointment(ctx: ToolContext, appointment_id: int, patient_phone: str) -> dict[str, Any]:
    def run(ctx: ToolContext) -> dict[str, Any]:
        appt = _owned_appointment(ctx, appointment_id, patient_phone)
        appt.status = "cancelled"
        ctx.session.add(appt)
        ctx.session.commit()
        result: dict[str, Any] = {"cancelled": _describe(appt)}
        if appt.start - ctx.now < timedelta(hours=24):
            result["note"] = "Less than 24 hours' notice; mention the cancellation policy kindly."
        return result

    appt = _owned_appointment(ctx, appointment_id, patient_phone)
    return _propose(ctx, {"action": "cancel", "appointment": _describe(appt)}, run)


@tool(
    "confirm_action",
    "Carry out a booking, reschedule or cancellation the caller just said yes to.",
    {"action_id": {"type": "string", "description": "action_id from the pending result"}},
    ["action_id"],
)
def confirm_action(ctx: ToolContext, action_id: str) -> dict[str, Any]:
    pending = ctx.pending.get(action_id)
    if not pending:
        raise SchedulingError(f"No pending action '{action_id}'. Propose the change again.")
    if pending.turn >= ctx.turn:
        raise SchedulingError(
            "The caller hasn't answered yet. Read the details back as a question, end your turn and wait."
        )
    del ctx.pending[action_id]
    return pending.run(ctx)


@lru_cache
def clinic_info() -> dict[str, str]:
    sections = re.split(r"^## +(\w+)\s*$", CLINIC_INFO_PATH.read_text(encoding="utf-8"), flags=re.M)
    return {key: text.strip() for key, text in zip(sections[1::2], sections[2::2], strict=True)}


@tool(
    "get_clinic_info",
    "Look up clinic facts: location, parking, insurance, payment, policies, emergencies, children, contact.",
    # The enum is filled in below, once clinic-info.md has been read
    {"topic": {"type": "string"}},
    ["topic"],
)
def get_clinic_info(ctx: ToolContext, topic: str) -> dict[str, Any]:
    info = clinic_info()
    if topic not in info:
        raise SchedulingError(f"Unknown topic '{topic}'. Valid: {', '.join(info)}.")
    return {"topic": topic, "info": info[topic]}


TOOLS["get_clinic_info"].parameters["properties"]["topic"]["enum"] = list(clinic_info())


@tool(
    "handoff_to_human",
    "Ask staff to call the caller back: when they want a person, have a complaint, a medical question, or "
    "something the tools can't do.",
    {
        "reason": {"type": "string", "description": "One sentence for staff"},
        "patient_name": {"type": "string"},
        "patient_phone": _PHONE,
    },
    ["reason", "patient_phone"],
)
def handoff_to_human(ctx: ToolContext, reason: str, patient_phone: str, patient_name: str = "") -> dict[str, Any]:
    handoff = Handoff(
        created_at=ctx.now,
        patient_name=patient_name.strip(),
        patient_phone=normalize_phone(patient_phone),
        reason=reason.strip(),
    )
    ctx.session.add(handoff)
    ctx.session.commit()
    return {"handoff_id": handoff.id, "message": "Staff will call back during opening hours."}
