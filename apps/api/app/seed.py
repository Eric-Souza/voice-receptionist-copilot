"""Demo bookings so the calendar isn't empty: roughly a third of the next two weeks' slots.

python -m app.seed           seed if there are no appointments yet
python -m app.seed --reset   delete all appointments and handoffs, then seed
"""

import random
import sys
from datetime import datetime, timedelta

from sqlmodel import delete, select

from app.clinic import DENTISTS
from app.db import init_db, new_session
from app.models import Appointment, Handoff
from app.scheduling import book, free_slots

PATIENTS = [
    ("Emily Carter", "5125550101"),
    ("Michael Johnson", "5125550102"),
    ("Sarah Thompson", "5125550103"),
    ("David Martinez", "5125550104"),
    ("Jessica Lee", "5125550105"),
    ("Chris Walker", "5125550106"),
    ("Ashley Brooks", "5125550107"),
    ("Daniel Kim", "5125550108"),
]
FILL_RATE = 0.35
DAYS = 14


def seed(now: datetime | None = None, rng: random.Random | None = None) -> int:
    from app.agent import clinic_now

    now = now or clinic_now()
    rng = rng or random.Random(42)
    count = 0
    with new_session() as session:
        for offset in range(DAYS):
            day = now.date() + timedelta(days=offset)
            for dentist in DENTISTS.values():
                for start, _ in free_slots(session, "checkup", day, now, dentist.id):
                    if rng.random() > FILL_RATE:
                        continue
                    service = rng.choice(dentist.services)
                    # Longer services may not fit where a check-up does; skip those slots
                    if (start, dentist.id) not in free_slots(session, service, day, now, dentist.id):
                        continue
                    name, phone = rng.choice(PATIENTS)
                    book(
                        session,
                        patient_name=name,
                        patient_phone=phone,
                        service=service,
                        start=start,
                        dentist_id=dentist.id,
                        now=now,
                    )
                    count += 1
    return count


def seed_if_empty() -> int:
    with new_session() as session:
        if session.exec(select(Appointment).limit(1)).first():
            return 0
    return seed()


def reset() -> int:
    with new_session() as session:
        session.exec(delete(Appointment))
        session.exec(delete(Handoff))
        session.commit()
    return seed()


if __name__ == "__main__":
    init_db()
    n = reset() if "--reset" in sys.argv else seed_if_empty()
    print(f"Seeded {n} appointments.")
