import { describe, expect, it } from "vitest";
import { addDays, clock, minutes, mondayOf, weekday } from "./dates";

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
    expect(clock(810)).toBe("13:30");
  });
});
