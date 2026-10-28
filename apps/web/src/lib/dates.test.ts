import { describe, expect, it } from "vitest";
import { addDays, clock, dayLabel, minutes, mondayOf, weekLabel, weekday } from "./dates";

describe("dates", () => {
  it("does week arithmetic on clinic dates", () => {
    expect(weekday("2026-10-28")).toBe(2); // a Wednesday
    expect(mondayOf("2026-10-28")).toBe("2026-10-26");
    expect(mondayOf("2026-11-01")).toBe("2026-10-26"); // Sunday belongs to the week before
    expect(addDays("2026-10-26", 7)).toBe("2026-11-02");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
  });

  it("reads and writes clock times", () => {
    expect(minutes("13:30")).toBe(810);
    expect(minutes("2026-10-29T08:00")).toBe(480);
    expect(clock(810)).toBe("1:30 pm");
    expect(clock(480)).toBe("8 am");
    expect(clock("2026-10-29T12:00")).toBe("12 pm");
  });

  it("labels days and weeks the US way", () => {
    expect(dayLabel("2026-10-29")).toEqual({ weekday: "Thu", date: "10/29" });
    expect(weekLabel("2026-10-26")).toBe("Oct 26 – Nov 1, 2026");
    expect(weekLabel("2026-12-28")).toBe("Dec 28 – Jan 3, 2027");
  });
});
