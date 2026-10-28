// Calendar arithmetic on the clinic's local "YYYY-MM-DD" / "HH:MM" strings. Done in UTC so the browser's own time
// zone (and its daylight saving changes) never shifts a day.

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function utc(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function addDays(day: string, n: number): string {
  const date = utc(day);
  date.setUTCDate(date.getUTCDate() + n);
  return date.toISOString().slice(0, 10);
}

/** Monday = 0, like the API's opening hours. */
export function weekday(day: string): number {
  return (utc(day).getUTCDay() + 6) % 7;
}

export const mondayOf = (day: string) => addDays(day, -weekday(day));

/** Minutes since midnight of "HH:MM" or of the time in "YYYY-MM-DDTHH:MM". */
export function minutes(time: string): number {
  const [h, m] = time.slice(time.indexOf("T") + 1).split(":").map(Number);
  return h * 60 + m;
}

/** 12-hour time, the way a US clinic writes it: "8 am", "1:30 pm". Takes minutes since midnight or a time string. */
export function clock(time: number | string): string {
  const mins = typeof time === "number" ? time : minutes(time);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${h % 12 || 12}${m ? `:${String(m).padStart(2, "0")}` : ""} ${h < 12 ? "am" : "pm"}`;
}

export function dayLabel(day: string): { weekday: string; date: string } {
  const date = utc(day);
  return { weekday: WEEKDAYS[weekday(day)], date: `${date.getUTCMonth() + 1}/${date.getUTCDate()}` };
}

export function weekLabel(monday: string): string {
  const fmt = (day: string) =>
    utc(day).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return `${fmt(monday)} – ${fmt(addDays(monday, 6))}, ${addDays(monday, 6).slice(0, 4)}`;
}
