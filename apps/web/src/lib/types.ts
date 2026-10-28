// The /ws/voice protocol (see apps/api/app/voice/call.py) and the REST shapes the UI uses

export type Language = "pt" | "en";

export interface Timings {
  stt_ms?: number;
  llm_first_text_ms?: number;
  first_audio_ms?: number;
  total_ms?: number;
  tools?: string[];
}

export type AudioKind = "reply" | "greeting" | "apology" | "filler";

export type ServerEvent =
  | { type: "ready"; language: Language; sample_rate: number }
  | { type: "vad"; speaking: boolean }
  | { type: "transcript"; text: string; language: Language | null }
  | { type: "reply"; delta: string }
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; result: Record<string, unknown> }
  | { type: "audio"; text: string; bytes: number; kind: AudioKind }
  | { type: "interrupt"; heard: string }
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
  now: string;
  slot_minutes: number;
  hours: Record<string, [string, string][]>; // Monday = "0"
  dentists: { id: string; name: string; services: string[] }[];
  services: { key: string; name_pt: string; name_en: string; minutes: number; price_brl: number }[];
}
