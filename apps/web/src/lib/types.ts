// The /ws/voice protocol (see apps/api/app/voice/call.py) and the REST shapes the UI uses

export interface Timings {
  stt_ms?: number;
  llm_first_text_ms?: number;
  first_audio_ms?: number;
  total_ms?: number;
  tools?: string[];
  input_guard_ms?: number;
  input_guard_wait_ms?: number; // how long the input check held up words the model had already written
  output_guard_ms?: number[];
  blocked?: string; // the rail that blocked this turn
}

export type AudioKind = "reply" | "greeting" | "apology" | "filler" | "guardrail";

export interface GuardCheck {
  stage: "input" | "output";
  allowed: boolean;
  rail: string | null;
  ms: number;
  waited_ms?: number;
  text?: string; // output: the sentence checked
  error?: string; // the check failed and was let through
}

export type ServerEvent =
  | { type: "ready"; sample_rate: number }
  | { type: "vad"; speaking: boolean }
  | { type: "transcript"; text: string }
  | { type: "reply"; delta: string }
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; result: Record<string, unknown> }
  | ({ type: "guardrail" } & GuardCheck)
  | { type: "audio"; text: string; bytes: number; kind: AudioKind }
  | { type: "interrupt"; heard: string; transcribing?: boolean }
  | { type: "turn_done"; text: string; timings: Timings }
  | { type: "error"; message: string };

export interface Appointment {
  id: number;
  patient_name: string;
  dentist_id: string;
  service: string;
  start: string; // clinic local time, "YYYY-MM-DDTHH:MM"
  end: string;
}

export interface Clinic {
  name: string;
  city: string;
  now: string;
  slot_minutes: number;
  hours: Record<string, [string, string][]>; // Monday = "0"
  dentists: { id: string; name: string; services: string[] }[];
  services: { key: string; name: string; minutes: number; price_usd: number }[];
}
