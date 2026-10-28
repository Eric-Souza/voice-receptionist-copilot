// Folds the server's event stream into what the UI shows: one entry per exchange, with its tool calls and timings.
// Pure, so it's unit-tested without a browser; `at` is the client clock (performance.now()) when an event arrived.

import type { ServerEvent, Timings } from "./types";

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: Record<string, unknown>;
  calledAt: number; // ms after the caller stopped talking
  ms?: number; // how long it took, call to result
}

export interface Turn {
  id: number;
  kind: "greeting" | "exchange";
  caller?: { text: string };
  sofia: string; // what she said (or is saying, while it streams)
  filler?: string; // the "one moment" said while a tool ran
  status: "replying" | "done" | "interrupted" | "failed";
  heard?: string; // when interrupted: what the caller heard before cutting in
  tools: ToolCall[];
  timings?: Timings;
  error?: string;
  speechEndedAt: number; // client clock
}

export type AppointmentChange = { id: number; action: "booked" | "rescheduled" | "cancelled"; start?: string };

export interface CallState {
  status: "idle" | "connecting" | "live" | "ended";
  callerSpeaking: boolean;
  turns: Turn[];
  // Bumped on every confirmed change, so the calendar knows to reload and what to highlight
  change?: AppointmentChange & { seq: number };
  error?: string;
  speechEndedAt?: number;
  nextId: number;
}

export type CallAction =
  | { type: "connect" }
  | { type: "closed"; error?: string }
  | { type: "event"; event: ServerEvent; at: number };

export const initialCall: CallState = {
  status: "idle",
  callerSpeaking: false,
  turns: [],
  nextId: 1,
};

const CHANGES = ["booked", "rescheduled", "cancelled"] as const;

/** The appointment a confirm_action result changed, if it changed one. */
export function appointmentChange(result: Record<string, unknown> | undefined): AppointmentChange | undefined {
  for (const action of CHANGES) {
    const details = result?.[action] as { appointment_id?: number; start?: string } | undefined;
    if (details?.appointment_id != null) return { id: details.appointment_id, action, start: details.start };
  }
  return undefined;
}

function updateLast(state: CallState, update: (turn: Turn) => Turn): CallState {
  const last = state.turns.at(-1);
  if (!last || last.kind !== "exchange" || last.status !== "replying") return state;
  return { ...state, turns: [...state.turns.slice(0, -1), update(last)] };
}

export function callReducer(state: CallState, action: CallAction): CallState {
  if (action.type === "connect") return { ...initialCall, status: "connecting" };
  if (action.type === "closed") {
    const turns = state.turns.map((t) => (t.status === "replying" ? { ...t, status: "done" as const } : t));
    return { ...state, status: "ended", callerSpeaking: false, turns, error: state.error ?? action.error };
  }

  const { event, at } = action;
  switch (event.type) {
    case "ready":
      return { ...state, status: "live" };

    case "vad":
      return { ...state, callerSpeaking: event.speaking, speechEndedAt: event.speaking ? state.speechEndedAt : at };

    case "transcript": {
      if (!event.text) return state; // just noise
      const turn: Turn = {
        id: state.nextId,
        kind: "exchange",
        caller: { text: event.text },
        sofia: "",
        status: "replying",
        tools: [],
        speechEndedAt: state.speechEndedAt ?? at,
      };
      // (The caller got through, so an earlier "transcription failed" no longer applies)
      return { ...state, error: undefined, turns: [...state.turns, turn], nextId: state.nextId + 1 };
    }

    case "reply":
      return updateLast(state, (t) => ({ ...t, sofia: t.sofia + event.delta }));

    case "audio":
      if (event.kind === "greeting") {
        const greeting: Turn = {
          id: state.nextId,
          kind: "greeting",
          sofia: event.text,
          status: "done",
          tools: [],
          speechEndedAt: at,
        };
        return { ...state, turns: [...state.turns, greeting], nextId: state.nextId + 1 };
      }
      if (event.kind === "filler") return updateLast(state, (t) => ({ ...t, filler: event.text }));
      if (event.kind === "apology") return updateLast(state, (t) => ({ ...t, sofia: t.sofia || event.text }));
      return state;

    case "tool_call":
      return updateLast(state, (t) => ({
        ...t,
        tools: [
          ...t.tools,
          { id: event.id, name: event.name, arguments: event.arguments, calledAt: Math.round(at - t.speechEndedAt) },
        ],
      }));

    case "tool_result": {
      let turnStart = at;
      const next = updateLast(state, (t) => {
        turnStart = t.speechEndedAt;
        return {
          ...t,
          tools: t.tools.map((c) =>
            c.id === event.id ? { ...c, result: event.result, ms: Math.round(at - turnStart - c.calledAt) } : c,
          ),
        };
      });
      const change = event.name === "confirm_action" ? appointmentChange(event.result) : undefined;
      return change ? { ...next, change: { ...change, seq: (state.change?.seq ?? 0) + 1 } } : next;
    }

    case "interrupt": {
      const last = state.turns.at(-1);
      // Cut in before their words were transcribed: there's no exchange for them yet, and the one shown before
      // was heard in full
      if (event.transcribing || !last || last.kind !== "exchange" || last.status === "interrupted") return state;
      // Cut off before saying or doing anything: the server sends the caller's words again together with what
      // they went on to say, so this exchange is replaced by the next one
      if (last.status === "replying" && !event.heard && last.tools.length === 0) {
        return { ...state, turns: state.turns.slice(0, -1) };
      }
      // (A finished reply can still be cut off while its last sentences play)
      const interrupted: Turn = { ...last, status: "interrupted", heard: event.heard };
      return { ...state, turns: [...state.turns.slice(0, -1), interrupted] };
    }

    case "turn_done":
      return updateLast(state, (t) => ({
        ...t,
        sofia: event.text || t.sofia,
        status: t.error && !event.text ? "failed" : "done",
        timings: event.timings,
      }));

    case "error": {
      const last = state.turns.at(-1);
      if (last?.kind === "exchange" && last.status === "replying") {
        return updateLast(state, (t) => ({ ...t, error: event.message }));
      }
      return { ...state, error: event.message };
    }
  }
}

export function median(values: number[]): number | undefined {
  if (!values.length) return undefined;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}
