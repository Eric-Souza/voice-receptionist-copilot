import { describe, expect, it } from "vitest";
import { type CallState, callReducer, initialCall, median } from "./callState";
import type { ServerEvent } from "./types";

function play(events: [number, ServerEvent][], state: CallState = initialCall): CallState {
  return events.reduce((s, [at, event]) => callReducer(s, { type: "event", event, at }), state);
}

const GREETED: [number, ServerEvent][] = [
  [0, { type: "ready", language: "pt", sample_rate: 24000 }],
  [10, { type: "audio", text: "Clínica Sorriso Paulista, aqui é a Sofia.", bytes: 100, kind: "greeting" }],
];

const CONFIRMED = {
  booked: { appointment_id: 7, patient_name: "Ana Paula", start: "2026-10-29T08:00", dentist: "Dra. Ana Ribeiro" },
};

describe("callReducer", () => {
  it("builds an exchange from a turn's events", () => {
    const state = play([
      ...GREETED,
      [1000, { type: "vad", speaking: true }],
      [2000, { type: "vad", speaking: false }],
      [2400, { type: "transcript", text: "Pode ser, confirma.", language: "pt" }],
      [2900, { type: "tool_call", id: "c1", name: "confirm_action", arguments: { action_id: "A1" } }],
      [2950, { type: "audio", text: "Só um instante.", bytes: 10, kind: "filler" }],
      [2960, { type: "tool_result", id: "c1", name: "confirm_action", result: CONFIRMED }],
      [3500, { type: "reply", delta: "Pronto, " }],
      [3550, { type: "reply", delta: "está marcado." }],
      [4200, { type: "turn_done", text: "Pronto, está marcado.", timings: { stt_ms: 400, total_ms: 2200 } }],
    ]);

    expect(state.status).toBe("live");
    expect(state.turns.map((t) => t.kind)).toEqual(["greeting", "exchange"]);
    const turn = state.turns[1];
    expect(turn.caller).toEqual({ text: "Pode ser, confirma.", language: "pt" });
    expect(turn.sofia).toBe("Pronto, está marcado.");
    expect(turn.filler).toBe("Só um instante.");
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
      [100, { type: "transcript", text: "", language: null }],
      [200, { type: "transcript", text: "Quais horários?", language: "pt" }],
      [300, { type: "tool_call", id: "c1", name: "find_appointments", arguments: {} }],
      [400, { type: "tool_result", id: "c1", name: "find_appointments", result: { appointments: [] } }],
    ]);
    expect(state.turns).toHaveLength(2);
    expect(state.change).toBeUndefined();
  });

  it("keeps what was heard when the caller cuts in", () => {
    const state = play([
      ...GREETED,
      [100, { type: "transcript", text: "Quinta?", language: "pt" }],
      [200, { type: "reply", delta: "Temos três horários na quinta. Às 8h, às 9h" }],
      [900, { type: "interrupt", heard: "Temos três horários na quinta." }],
    ]);
    expect(state.turns[1]).toMatchObject({ status: "interrupted", heard: "Temos três horários na quinta." });

    // A late event from the cancelled turn doesn't touch it
    const later = play([[950, { type: "reply", delta: " ou às 10h." }]], state);
    expect(later.turns[1].sofia).toBe("Temos três horários na quinta. Às 8h, às 9h");
  });

  it("drops an exchange cut off before Sofia said anything, since it comes back merged", () => {
    const state = play([
      ...GREETED,
      [100, { type: "transcript", text: "Quero marcar uma limpeza", language: "pt" }],
      [300, { type: "interrupt", heard: "" }],
      [2000, { type: "transcript", text: "Quero marcar uma limpeza na quinta.", language: "pt" }],
    ]);
    expect(state.turns.map((t) => t.caller?.text)).toEqual([undefined, "Quero marcar uma limpeza na quinta."]);
  });

  it("marks a failed turn and ends the call cleanly", () => {
    let state = play([
      ...GREETED,
      [100, { type: "transcript", text: "Oi", language: "pt" }],
      [200, { type: "error", message: "The language model call failed" }],
      [300, { type: "audio", text: "Desculpe, tive um probleminha aqui.", bytes: 10, kind: "apology" }],
      [400, { type: "turn_done", text: "", timings: {} }],
    ]);
    expect(state.turns[1]).toMatchObject({ status: "failed", sofia: "Desculpe, tive um probleminha aqui." });

    state = callReducer(state, { type: "closed" });
    expect(state.status).toBe("ended");
    expect(callReducer(state, { type: "connect", language: "en" })).toMatchObject({ turns: [], language: "en" });
  });
});

describe("median", () => {
  it("takes the middle value", () => {
    expect(median([])).toBeUndefined();
    expect(median([300, 100, 200])).toBe(200);
    expect(median([100, 200, 400, 300])).toBe(250);
  });
});
