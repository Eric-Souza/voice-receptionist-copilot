import { describe, expect, it } from "vitest";
import { type CallState, callReducer, initialCall, median } from "./callState";
import type { ServerEvent } from "./types";

function play(events: [number, ServerEvent][], state: CallState = initialCall): CallState {
  return events.reduce((s, [at, event]) => callReducer(s, { type: "event", event, at }), state);
}

const GREETED: [number, ServerEvent][] = [
  [0, { type: "ready", sample_rate: 24000 }],
  [10, { type: "audio", text: "Thanks for calling Brightside Dental, this is Sofia.", bytes: 100, kind: "greeting" }],
];

const CONFIRMED = {
  booked: { appointment_id: 7, patient_name: "Jane Doe", start: "2026-10-29T08:00", dentist: "Dr. Anna Reed" },
};

describe("callReducer", () => {
  it("builds an exchange from a turn's events", () => {
    const state = play([
      ...GREETED,
      [1000, { type: "vad", speaking: true }],
      [2000, { type: "vad", speaking: false }],
      [2400, { type: "transcript", text: "Yes, go ahead." }],
      [2900, { type: "tool_call", id: "c1", name: "confirm_action", arguments: { action_id: "A1" } }],
      [2950, { type: "audio", text: "One moment.", bytes: 10, kind: "filler" }],
      [2960, { type: "tool_result", id: "c1", name: "confirm_action", result: CONFIRMED }],
      [3500, { type: "reply", delta: "Done, " }],
      [3550, { type: "reply", delta: "you're booked." }],
      [4200, { type: "turn_done", text: "Done, you're booked.", timings: { stt_ms: 400, total_ms: 2200 } }],
    ]);

    expect(state.status).toBe("live");
    expect(state.turns.map((t) => t.kind)).toEqual(["greeting", "exchange"]);
    const turn = state.turns[1];
    expect(turn.caller).toEqual({ text: "Yes, go ahead." });
    expect(turn.sofia).toBe("Done, you're booked.");
    expect(turn.filler).toBe("One moment.");
    expect(turn.status).toBe("done");
    expect(turn.timings?.stt_ms).toBe(400);
    // Tool times are relative to the end of the caller's speech
    expect(turn.tools).toEqual([
      expect.objectContaining({ name: "confirm_action", calledAt: 900, ms: 60, result: CONFIRMED }),
    ]);
    expect(state.change).toEqual({ id: 7, action: "booked", start: "2026-10-29T08:00", seq: 1 });
  });

  it("ignores noise and only flags changes from confirm_action", () => {
    const state = play([
      ...GREETED,
      [100, { type: "transcript", text: "" }],
      [200, { type: "transcript", text: "What times do you have?" }],
      [300, { type: "tool_call", id: "c1", name: "find_appointments", arguments: {} }],
      [400, { type: "tool_result", id: "c1", name: "find_appointments", result: { appointments: [] } }],
    ]);
    expect(state.turns).toHaveLength(2);
    expect(state.change).toBeUndefined();
  });

  it("keeps what was heard when the caller cuts in", () => {
    const state = play([
      ...GREETED,
      [100, { type: "transcript", text: "Thursday?" }],
      [200, { type: "reply", delta: "We have three times on Thursday. At 8, 9" }],
      [900, { type: "interrupt", heard: "We have three times on Thursday." }],
    ]);
    expect(state.turns[1]).toMatchObject({ status: "interrupted", heard: "We have three times on Thursday." });

    // A late event from the cancelled turn doesn't touch it
    const later = play([[950, { type: "reply", delta: " or 10 am." }]], state);
    expect(later.turns[1].sofia).toBe("We have three times on Thursday. At 8, 9");
  });

  it("drops an exchange cut off before Sofia said anything, since it comes back merged", () => {
    const state = play([
      ...GREETED,
      [100, { type: "transcript", text: "I'd like a cleaning" }],
      [300, { type: "interrupt", heard: "" }],
      [2000, { type: "transcript", text: "I'd like a cleaning on Thursday." }],
    ]);
    expect(state.turns.map((t) => t.caller?.text)).toEqual([undefined, "I'd like a cleaning on Thursday."]);
  });

  it("leaves a finished reply alone when the caller cuts in before being transcribed", () => {
    const state = play([
      ...GREETED,
      [100, { type: "transcript", text: "Do you have parking?" }],
      [200, { type: "turn_done", text: "Yes, behind the building.", timings: {} }],
      [900, { type: "interrupt", heard: "", transcribing: true }],
    ]);
    expect(state.turns[1]).toMatchObject({ status: "done", sofia: "Yes, behind the building." });
  });

  it("keeps the server's error over the close code that follows it", () => {
    let state = play([[0, { type: "error", message: "The voice models aren't downloaded" }]]);
    state = callReducer(state, { type: "closed", error: "Connection closed (1011)" });
    expect(state.error).toBe("The voice models aren't downloaded");
  });

  it("marks a failed turn and ends the call cleanly", () => {
    let state = play([
      ...GREETED,
      [100, { type: "transcript", text: "Hi" }],
      [200, { type: "error", message: "The language model call failed" }],
      [300, { type: "audio", text: "Sorry, I had a problem on my end. Could you say that again?", bytes: 10, kind: "apology" }],
      [400, { type: "turn_done", text: "", timings: {} }],
    ]);
    expect(state.turns[1]).toMatchObject({ status: "failed", sofia: "Sorry, I had a problem on my end. Could you say that again?" });

    state = callReducer(state, { type: "closed" });
    expect(state.status).toBe("ended");
    expect(callReducer(state, { type: "connect" })).toMatchObject({ turns: [] });
  });
});

describe("median", () => {
  it("takes the middle value", () => {
    expect(median([])).toBeUndefined();
    expect(median([300, 100, 200])).toBe(200);
    expect(median([100, 200, 400, 300])).toBe(250);
  });
});
