import { useEffect, useRef } from "react";
import type { Turn } from "../lib/callState";
import { Panel } from "./Panel";

export function Transcript({ turns, callerSpeaking }: { turns: Turn[]; callerSpeaking: boolean }) {
  const end = useRef<HTMLDivElement>(null);
  const last = turns.at(-1);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns.length, last?.sofia]);

  return (
    <Panel title="Transcript" className="h-[28rem]">
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {turns.length === 0 && (
          <p className="pt-16 text-center text-sm text-slate-400">
            Start a call and talk to Sofia: book, move or cancel a dentist appointment.
          </p>
        )}
        {turns.map((turn) => (
          <div key={turn.id} className="space-y-2">
            {turn.caller && (
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-teal-700 px-3.5 py-2 text-white">
                  {turn.caller.text}
                </div>
              </div>
            )}
            <SofiaLine turn={turn} />
          </div>
        ))}
        {callerSpeaking && (
          <div className="flex justify-end">
            <div className="rounded-2xl rounded-br-sm bg-teal-100 px-3.5 py-2 text-teal-800">
              <span className="animate-pulse">● ● ●</span>
            </div>
          </div>
        )}
        <div ref={end} />
      </div>
    </Panel>
  );
}

function SofiaLine({ turn }: { turn: Turn }) {
  const thinking = turn.status === "replying" && !turn.sofia;
  if (turn.status === "interrupted" && !turn.sofia && !turn.filler) return null;
  return (
    <div className="flex flex-col items-start gap-1">
      {turn.filler && !turn.sofia && <div className="text-sm text-slate-500 italic">{turn.filler}</div>}
      {(turn.sofia || thinking) && (
        <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-slate-100 px-3.5 py-2 text-slate-800">
          {turn.status === "interrupted" ? <Interrupted text={turn.sofia} heard={turn.heard ?? ""} /> : turn.sofia}
          {thinking && <span className="animate-pulse text-slate-400">…</span>}
          {turn.status === "replying" && turn.sofia && <span className="ml-0.5 animate-pulse text-slate-400">▍</span>}
        </div>
      )}
      {turn.status === "interrupted" && (
        <div className="text-xs text-amber-700">✋ You cut in</div>
      )}
      {turn.error && (
        <div className="line-clamp-2 max-w-[85%] text-xs text-rose-700" title={turn.error}>
          ⚠ {/\b429\b/.test(turn.error) ? "The language model is rate-limited right now (Groq free tier)" : turn.error}
        </div>
      )}
    </div>
  );
}

/** The heard part normally, the rest struck through: Sofia never said it, and the model knows. */
function Interrupted({ text: raw, heard: rawHeard }: { text: string; heard: string }) {
  // The server joins the sentences it sent with single spaces
  const [text, heard] = [raw, rawHeard].map((s) => s.replace(/\s+/g, " ").trim());
  const cut = heard && text.startsWith(heard) ? heard.length : 0;
  return (
    <>
      {text.slice(0, cut)}
      <span className="text-slate-400 line-through decoration-slate-300">{text.slice(cut)}</span>
    </>
  );
}
