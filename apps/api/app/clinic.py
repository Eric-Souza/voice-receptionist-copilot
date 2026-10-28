"""The fictional clinic: services, dentists, opening hours. Static, so it lives in code rather than the DB."""

from dataclasses import dataclass
from datetime import time

CLINIC_NAME = "Brightside Dental"
CLINIC_CITY = "Austin, Texas"


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    minutes: int
    price_usd: int  # 0 = free


@dataclass(frozen=True)
class Dentist:
    id: str
    name: str
    services: tuple[str, ...]


SERVICES = {
    s.key: s
    for s in [
        Service("checkup", "Check-up", 30, 0),
        Service("cleaning", "Cleaning", 45, 120),
        Service("filling", "Filling", 60, 180),
        Service("whitening", "Whitening", 60, 450),
        Service("root_canal", "Root canal", 90, 1100),
        Service("extraction", "Extraction", 45, 250),
        Service("braces_adjustment", "Braces adjustment", 30, 150),
    ]
}

DENTISTS = {
    d.id: d
    for d in [
        Dentist("anna", "Dr. Anna Reed", ("checkup", "cleaning", "filling", "whitening")),
        Dentist("brian", "Dr. Brian Tanaka", ("checkup", "filling", "root_canal", "extraction")),
        Dentist("carla", "Dr. Carla Mendez", ("checkup", "cleaning", "braces_adjustment")),
    ]
}

# Opening hours per weekday (Monday = 0), as (open, close) blocks; lunch is the gap between blocks
HOURS: dict[int, list[tuple[time, time]]] = {
    **{day: [(time(8), time(12)), (time(13), time(18))] for day in range(5)},
    5: [(time(8), time(12))],
    6: [],
}

# Appointments start on this grid
SLOT_MINUTES = 30
# How soon a caller can book: no slots starting within this many minutes of now
MIN_NOTICE_MINUTES = 60
# How far ahead the calendar is open
BOOKING_HORIZON_DAYS = 60
