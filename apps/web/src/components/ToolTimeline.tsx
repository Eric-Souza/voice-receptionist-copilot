import { useEffect, useRef } from "react";
import type { ToolCall, Turn } from "../lib/callState";
import { Panel } from "./Panel";

const TOOL_STYLE: Record<string, string> = {
  check_availability: "bg-sky-500",
  book_appointment: "bg-violet-500",
  reschedule_appointment: "bg-violet-500",
  cancel_appointment: "bg-violet-500",
  confirm_action: "bg-emerald-600",
  find_appointments: "bg-indigo-500",
  get_clinic_info: "bg-slate-500",
  handoff_to_human: "bg-rose-500",
};

const seconds = (ms: number) => `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;

export function ToolTimeline({ turns }: { turns: Turn[] }) {
  const withTools = turns.filter((t) => t.tools.length);
  const end = useRef<HTMLDivElement>(null);
  const count = withTools.reduce((n, t) => n + t.tools.length + (t.tools.at(-1)?.result ? 1 : 0), 0);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [count]);

  return (
    <Panel title="Tool calls" className="h-[28rem]">
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-3">
        {withTools.length === 0 && (
          <p className="pt-16 text-center text-sm text-slate-400">
            The tools Sofia uses (availability, booking, confirming) show up here as she calls them.
          </p>
        )}
        {withTools.map((turn) => (
          <TurnTools key={turn.id} turn={turn} />
        ))}
        <div ref={end} />
      </div>
    </Panel>
  );
}

function TurnTools({ turn }: { turn: Turn }) {
  // One shared scale per turn: from the end of the caller's speech to the end of the reply
  const lastTool = Math.max(...turn.tools.map((c) => c.calledAt + (c.ms ?? 0)));
  const span = Math.max(turn.timings?.total_ms ?? 0, lastTool, 1);
  const pct = (ms: number) => `${Math.min(100, (ms / span) * 100)}%`;
  const firstAudio = turn.timings?.first_audio_ms;

  return (
    <div className="space-y-1.5">
      <div className="truncate text-xs text-slate-500">“{turn.caller?.text}”</div>
      {turn.tools.map((call) => (
        <div key={call.id} className="rounded-lg border border-slate-100 bg-slate-50/60 p-2">
          <div className="flex items-baseline justify-between gap-2">
            <code className="text-[13px] font-semibold text-slate-800">{call.name}</code>
            <span className="shrink-0 font-mono text-[11px] text-slate-500">
              +{seconds(call.calledAt)} · {call.ms == null ? "running…" : `${call.ms} ms`}
            </span>
          </div>
          <div className="relative mt-1 h-1.5 rounded-full bg-slate-200">
            <div
              className={`absolute h-full rounded-full ${TOOL_STYLE[call.name] ?? "bg-slate-500"} ${call.ms == null ? "animate-pulse" : ""}`}
              style={{ left: pct(call.calledAt), width: `max(4px, ${pct(call.ms ?? span - call.calledAt)})` }}
            />
            {firstAudio != null && (
              <div className="absolute -top-0.5 h-2.5 w-0.5 bg-sky-600" style={{ left: pct(firstAudio) }} title="First audio" />
            )}
          </div>
          <Args call={call} />
        </div>
      ))}
    </div>
  );
}

function Args({ call }: { call: ToolCall }) {
  const args = Object.entries(call.arguments)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("  ");
  const outcome = summarize(call.result);
  return (
    <details className="mt-1 text-[11px] leading-snug">
      <summary className="cursor-pointer list-none font-mono text-slate-600 [&::-webkit-details-marker]:hidden">
        <span className="break-all">{args || "(no arguments)"}</span>
        {outcome && <div className={`mt-0.5 font-sans ${outcome.error ? "text-rose-700" : "text-emerald-700"}`}>→ {outcome.text}</div>}
      </summary>
      {call.result && (
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-slate-900 p-2 text-[10.5px] text-slate-100">
          {JSON.stringify(call.result, null, 2)}
        </pre>
      )}
    </details>
  );
}

/** A one-line reading of a tool result. */
function summarize(result?: Record<string, unknown>): { text: string; error?: boolean } | undefined {
  if (!result) return undefined;
  if (typeof result.error === "string") return { text: result.error, error: true };
  for (const action of ["booked", "rescheduled", "cancelled"]) {
    const d = result[action] as { appointment_id?: number; patient_name?: string; start?: string } | undefined;
    if (d) return { text: `${action} #${d.appointment_id} · ${d.patient_name} · ${d.start?.replace("T", " ")}` };
  }
  if (Array.isArray(result.slots)) return { text: `${result.slots.length} free slot${result.slots.length === 1 ? "" : "s"}` };
  if (Array.isArray(result.appointments)) return { text: `${result.appointments.length} appointment(s) found` };
  const pending = result.pending as { action_id?: string; action?: string } | undefined;
  if (pending) return { text: `proposed ${pending.action ?? "change"} ${pending.action_id}, waiting for a yes` };
  if (typeof result.topic === "string") return { text: `info: ${result.topic}` };
  if (result.handoff_id != null) return { text: `callback request #${result.handoff_id}` };
  return { text: "ok" };
}
