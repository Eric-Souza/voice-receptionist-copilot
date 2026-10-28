import { median, type Turn } from "../lib/callState";
import { Panel } from "./Panel";

// Each turn's bar, measured from the end of the caller's speech: transcription, then the model up to the first
// audio sent, then the rest of the reply
const SEGMENTS = [
  { key: "stt", label: "Speech to text", style: "bg-teal-500" },
  { key: "think", label: "To first audio", style: "bg-sky-500" },
  { key: "rest", label: "Rest of reply", style: "bg-slate-300" },
] as const;

const STATS = [
  { key: "stt_ms", label: "STT" },
  { key: "llm_first_text_ms", label: "First text" },
  { key: "first_audio_ms", label: "First audio" },
  { key: "total_ms", label: "Total" },
] as const;

// Roughly where a pause before an answer starts to feel long on the phone
const TARGET_MS = 1500;

export function LatencyPanel({ turns }: { turns: Turn[] }) {
  const timed = turns.filter((t) => t.timings?.total_ms != null);
  const span = Math.max(TARGET_MS * 1.5, ...timed.map((t) => t.timings!.total_ms!));
  const pct = (ms: number) => `${(ms / span) * 100}%`;

  return (
    <Panel title="Latency" className="h-[28rem]" aside={<span className="text-xs text-slate-400">median of {timed.length} turns</span>}>
      <div className="grid grid-cols-4 gap-2 px-4 pt-3">
        {STATS.map(({ key, label }) => {
          const value = median(timed.flatMap((t) => (t.timings?.[key] != null ? [t.timings[key]!] : [])));
          const highlight = key === "first_audio_ms";
          return (
            <div key={key} className={`rounded-lg px-2 py-1.5 ${highlight ? "bg-sky-50" : "bg-slate-50"}`}>
              <div className="text-[11px] text-slate-500">{label}</div>
              <div className={`font-mono text-lg ${highlight ? "font-semibold text-sky-800" : "text-slate-800"}`}>
                {value == null ? "–" : `${(value / 1000).toFixed(2)}s`}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex-1 space-y-1.5 overflow-y-auto px-4 py-3">
        {timed.length === 0 && <p className="py-4 text-center text-sm text-slate-400">Timings appear after each reply.</p>}
        {timed.map((turn, n) => {
          const t = turn.timings!;
          const stt = t.stt_ms ?? 0;
          const first = t.first_audio_ms ?? t.total_ms!;
          const parts = { stt, think: Math.max(0, first - stt), rest: Math.max(0, t.total_ms! - first) };
          return (
            <div key={turn.id} className="flex items-center gap-2" title={turn.caller?.text}>
              <span className="w-5 text-right font-mono text-[11px] text-slate-400">{n + 1}</span>
              <div className="relative flex h-3 flex-1 overflow-hidden rounded-sm bg-slate-100">
                {SEGMENTS.map((s) => (
                  <div key={s.key} className={s.style} style={{ width: pct(parts[s.key]) }} />
                ))}
                <div className="absolute inset-y-0 border-l border-dashed border-rose-400" style={{ left: pct(TARGET_MS) }} />
              </div>
              <span className="w-24 text-right font-mono text-[11px] text-slate-600">
                {(first / 1000).toFixed(2)}s{t.tools?.length ? ` · ${t.tools.length} tool${t.tools.length > 1 ? "s" : ""}` : ""}
              </span>
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-3 border-t border-slate-100 px-4 py-2 text-[11px] text-slate-500">
        {SEGMENTS.map((s) => (
          <span key={s.key} className="flex items-center gap-1">
            <span className={`size-2.5 rounded-sm ${s.style}`} /> {s.label}
          </span>
        ))}
        <span className="flex items-center gap-1">
          <span className="h-2.5 border-l border-dashed border-rose-400" /> {TARGET_MS / 1000}s
        </span>
      </div>
    </Panel>
  );
}
