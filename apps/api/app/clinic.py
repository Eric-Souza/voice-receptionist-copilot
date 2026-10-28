"""The fictional clinic: services, dentists, opening hours. Static, so it lives in code rather than the DB."""

from dataclasses import dataclass
from datetime import time

CLINIC_NAME = "Clínica Sorriso Paulista"


@dataclass(frozen=True)
class Service:
    key: str
    name_pt: str
    name_en: str
    minutes: int
    price_brl: int  # 0 = free


@dataclass(frozen=True)
class Dentist:
    id: str
    name: str
    services: tuple[str, ...]


SERVICES = {
    s.key: s
    for s in [
        Service("checkup", "Avaliação", "Check-up", 30, 0),
        Service("cleaning", "Limpeza", "Cleaning", 45, 180),
        Service("filling", "Restauração", "Filling", 60, 250),
        Service("whitening", "Clareamento", "Whitening", 60, 900),
        Service("root_canal", "Tratamento de canal", "Root canal", 90, 1200),
        Service("extraction", "Extração", "Extraction", 45, 350),
        Service("braces_adjustment", "Manutenção de aparelho", "Braces adjustment", 30, 200),
    ]
}

DENTISTS = {
    d.id: d
    for d in [
        Dentist("ana", "Dra. Ana Ribeiro", ("checkup", "cleaning", "filling", "whitening")),
        Dentist("bruno", "Dr. Bruno Tanaka", ("checkup", "filling", "root_canal", "extraction")),
        Dentist("carla", "Dra. Carla Mendes", ("checkup", "cleaning", "braces_adjustment")),
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
