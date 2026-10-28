from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


class Appointment(SQLModel, table=True):
    """Times are naive datetimes in the clinic's time zone (settings.clinic_timezone)."""

    id: int | None = Field(default=None, primary_key=True)
    patient_name: str
    # Digits only, so "(11) 98765-4321" and "11987654321" match
    patient_phone: str = Field(index=True)
    dentist_id: str = Field(index=True)
    service: str
    start: NaiveDatetime = Field(index=True)
    end: NaiveDatetime
    status: str = Field(default="booked")  # "booked" or "cancelled"
    notes: str = ""


class Handoff(SQLModel, table=True):
    """A caller who asked for a person, or whom the agent couldn't help; staff call them back."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: NaiveDatetime
    patient_name: str = ""
    patient_phone: str = ""
    reason: str
