export type Activity = "idle" | "connecting" | "listening" | "caller" | "thinking" | "sofia";

const ACTIVITY: Record<Activity, { label: string; style: string }> = {
  idle: { label: "Not in a call", style: "bg-slate-100 text-slate-500" },
  connecting: { label: "Connecting…", style: "bg-slate-100 text-slate-600" },
  listening: { label: "Listening", style: "bg-emerald-50 text-emerald-700" },
  caller: { label: "You're talking", style: "bg-emerald-100 text-emerald-800 font-semibold" },
  thinking: { label: "Thinking…", style: "bg-amber-50 text-amber-700" },
  sofia: { label: "Sofia is speaking", style: "bg-sky-100 text-sky-800" },
};

interface Props {
  inCall: boolean;
  activity: Activity;
  allowInterrupt: boolean;
  onAllowInterrupt: (allow: boolean) => void;
  level: number;
  onStart: () => void;
  onStop: () => void;
}

export function CallControls(props: Props) {
  const { label, style } = ACTIVITY[props.activity];
  // RMS of speech is around 0.02–0.2; a square root makes quiet talk visible
  const meter = Math.min(1, Math.sqrt(props.level * 6));
  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={props.inCall ? props.onStop : props.onStart}
        className={
          props.inCall
            ? "rounded-full border border-rose-300 bg-white px-5 py-2 font-medium text-rose-700 hover:bg-rose-50"
            : "rounded-full bg-teal-700 px-5 py-2 font-medium text-white shadow-sm hover:bg-teal-800"
        }
      >
        {props.inCall ? "End call" : "Start call"}
      </button>

      <label className="flex items-center gap-2 text-sm text-slate-700" title="Turn off if Sofia keeps interrupting herself (no headphones)">
        <input
          type="checkbox"
          checked={props.allowInterrupt}
          onChange={(e) => props.onAllowInterrupt(e.target.checked)}
          className="size-4 accent-teal-700"
        />
        Let me interrupt
      </label>

      <span className={`rounded-full px-3 py-1 text-sm ${style}`} role="status">
        {label}
      </span>

      <div className="flex h-2 w-24 overflow-hidden rounded-full bg-slate-200" aria-label="Mic level">
        <div className="bg-emerald-500 transition-[width] duration-100" style={{ width: `${props.inCall ? meter * 100 : 0}%` }} />
      </div>
    </div>
  );
}
