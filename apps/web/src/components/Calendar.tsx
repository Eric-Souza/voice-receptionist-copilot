import { useEffect, useState } from "react";
import { fetchAppointments } from "../lib/api";
import type { CallState } from "../lib/callState";
import { addDays, clock, dayLabel, minutes, mondayOf, weekday, weekLabel } from "../lib/dates";
import type { Appointment, Clinic } from "../lib/types";
import { Panel } from "./Panel";

const ROW_PX = 30; // per slot
const DENTIST_STYLES = [
  "bg-teal-100 border-teal-500 text-teal-900",
  "bg-indigo-100 border-indigo-500 text-indigo-900",
  "bg-amber-100 border-amber-500 text-amber-900",
];
const HIGHLIGHT_MS = 8000;

export function Calendar({ clinic, change }: { clinic: Clinic; change: CallState["change"] }) {
  const today = clinic.now.slice(0, 10);
  const [week, setWeek] = useState(() => mondayOf(today));
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [error, setError] = useState<string>();
  const [highlight, setHighlight] = useState<CallState["change"]>();

  // A confirmed change during the call: jump to its week, reload, and point at it for a few seconds
  useEffect(() => {
    if (!change) return;
    if (change.start) setWeek(mondayOf(change.start.slice(0, 10)));
    setHighlight(change);
    const timer = setTimeout(() => setHighlight(undefined), HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [change]);

  useEffect(() => {
    let current = true;
    fetchAppointments(week, 7)
      .then((rows) => current && (setAppointments(rows), setError(undefined)))
      .catch((err: Error) => current && setError(err.message));
    return () => {
      current = false;
    };
  }, [week, change?.seq]);

  const days = [0, 1, 2, 3, 4, 5, 6].filter((d) => clinic.hours[d]?.length).map((d) => addDays(week, d));
  const blocks = Object.values(clinic.hours).flat();
  const open = Math.min(...blocks.map(([a]) => minutes(a)));
  const close = Math.max(...blocks.map(([, b]) => minutes(b)));
  const slot = clinic.slot_minutes;
  const rows = (close - open) / slot;
  const y = (mins: number) => ((mins - open) / slot) * ROW_PX;
  const dentists = clinic.dentists.map((d) => d.id);
  const services = Object.fromEntries(clinic.services.map((s) => [s.key, s]));
  const nowMins = minutes(clinic.now);

  return (
    <Panel
      title="Bookings"
      aside={
        <div className="flex items-center gap-2 text-sm">
          {highlight && <ChangeNote change={highlight} />}
          <button type="button" aria-label="Previous week" onClick={() => setWeek(addDays(week, -7))} className="rounded px-2 hover:bg-slate-100">
            ‹
          </button>
          <span className="w-36 text-center text-slate-600">{weekLabel(week)}</span>
          <button type="button" aria-label="Next week" onClick={() => setWeek(addDays(week, 7))} className="rounded px-2 hover:bg-slate-100">
            ›
          </button>
          {week !== mondayOf(today) && (
            <button type="button" onClick={() => setWeek(mondayOf(today))} className="rounded border border-slate-200 px-2 text-xs hover:bg-slate-50">
              Today
            </button>
          )}
        </div>
      }
    >
      <div className="flex flex-wrap gap-3 px-4 pt-2 text-xs text-slate-600">
        {clinic.dentists.map((d, i) => (
          <span key={d.id} className="flex items-center gap-1">
            <span className={`size-3 rounded-sm border-l-4 ${DENTIST_STYLES[i % DENTIST_STYLES.length]}`} /> {d.name}
          </span>
        ))}
        {error && <span className="text-rose-700">Couldn't load bookings: {error}</span>}
      </div>

      <div className="overflow-x-auto px-4 pt-2 pb-4">
        <div className="grid min-w-[720px]" style={{ gridTemplateColumns: `3rem repeat(${days.length}, 1fr)` }}>
          <div />
          {days.map((day) => {
            const label = dayLabel(day);
            return (
              <div key={day} className={`pb-1 text-center text-xs ${day === today ? "font-semibold text-teal-700" : "text-slate-500"}`}>
                {label.weekday} <span className="text-slate-400">{label.date}</span>
              </div>
            );
          })}

          {/* Hour labels */}
          <div className="relative" style={{ height: rows * ROW_PX }}>
            {Array.from({ length: rows / (60 / slot) + 1 }, (_, h) => (
              <div key={h} className="absolute right-1.5 -translate-y-1/2 text-[10px] text-slate-400" style={{ top: h * ROW_PX * (60 / slot) }}>
                {clock(open + h * 60)}
              </div>
            ))}
          </div>

          {days.map((day) => {
            const hours = clinic.hours[weekday(day)] ?? [];
            const booked = appointments.filter((a) => a.start.startsWith(day));
            return (
              <div key={day} className="relative border-l border-slate-200" style={{ height: rows * ROW_PX }}>
                {Array.from({ length: rows }, (_, r) => (
                  <div
                    key={r}
                    className={`absolute inset-x-0 border-t ${r % (60 / slot) ? "border-slate-100" : "border-slate-200"}`}
                    style={{ top: r * ROW_PX }}
                  />
                ))}
                <Closed open={open} close={close} hours={hours} y={y} />
                {day < today && <div className="absolute inset-0 bg-slate-50/70" />}
                {day === today && nowMins > open && nowMins < close && (
                  <div className="absolute inset-x-0 z-20 border-t-2 border-rose-500" style={{ top: y(nowMins) }}>
                    <span className="absolute -top-1 -left-1 size-2 rounded-full bg-rose-500" />
                  </div>
                )}
                {booked.map((a) => {
                  const lane = Math.max(0, dentists.indexOf(a.dentist_id));
                  const lit = highlight && highlight.id === a.id && highlight.action !== "cancelled";
                  const service = services[a.service];
                  return (
                    <div
                      key={a.id}
                      title={`#${a.id} ${a.patient_name} · ${service?.name_en ?? a.service} · ${a.start.slice(11)}–${a.end.slice(11)} · ${clinic.dentists[lane]?.name}`}
                      className={`absolute z-10 overflow-hidden rounded-sm border-l-4 px-1 text-[10px] leading-tight shadow-sm ${DENTIST_STYLES[lane % DENTIST_STYLES.length]} ${lit ? "z-30 animate-pulse ring-2 ring-rose-500 ring-offset-1" : ""}`}
                      style={{
                        top: y(minutes(a.start)) + 1,
                        height: Math.max(ROW_PX - 2, y(minutes(a.end)) - y(minutes(a.start)) - 2),
                        left: `calc(${(lane / dentists.length) * 100}% + 1px)`,
                        width: `calc(${100 / dentists.length}% - 2px)`,
                      }}
                    >
                      <div className="truncate font-medium">{a.patient_name}</div>
                      <div className="truncate opacity-75">{service?.name_en ?? a.service}</div>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </Panel>
  );
}

/** Shades the parts of the day the clinic is shut: before opening, lunch, after closing. */
function Closed({ open, close, hours, y }: { open: number; close: number; hours: [string, string][]; y: (m: number) => number }) {
  const gaps: [number, number][] = [];
  let from = open;
  for (const [a, b] of hours) {
    if (minutes(a) > from) gaps.push([from, minutes(a)]);
    from = minutes(b);
  }
  if (from < close) gaps.push([from, close]);
  return gaps.map(([a, b]) => (
    <div
      key={a}
      className="absolute inset-x-0 bg-[repeating-linear-gradient(135deg,#f1f5f9_0_6px,#e2e8f0_6px_7px)]"
      style={{ top: y(a), height: y(b) - y(a) }}
    />
  ));
}

function ChangeNote({ change }: { change: NonNullable<CallState["change"]> }) {
  const styles = {
    booked: "bg-emerald-100 text-emerald-800",
    rescheduled: "bg-sky-100 text-sky-800",
    cancelled: "bg-rose-100 text-rose-800",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${styles[change.action]}`}>
      #{change.id} {change.action}
      {change.start && change.action !== "cancelled" ? ` · ${change.start.replace("T", " ")}` : ""}
    </span>
  );
}
