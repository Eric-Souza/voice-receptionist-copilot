import type { Appointment, Clinic, Language } from "./types";

// Empty in development (Vite proxies /api and /ws); the API's origin in a deployed build
const BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

async function get<T>(path: string): Promise<T> {
  const response = await fetch(BASE + path);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json() as Promise<T>;
}

export const fetchClinic = () => get<Clinic>("/api/clinic");

export const fetchAppointments = (start: string, days = 7) =>
  get<Appointment[]>(`/api/appointments?start=${start}&days=${days}`);

export function voiceUrl(language: Language): string {
  const origin = BASE || location.origin;
  return `${origin.replace(/^http/, "ws")}/ws/voice?language=${language}`;
}
