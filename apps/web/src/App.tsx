import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Calendar } from "./components/Calendar";
import { type Activity, CallControls } from "./components/CallControls";
import { LatencyPanel } from "./components/LatencyPanel";
import { ToolTimeline } from "./components/ToolTimeline";
import { Transcript } from "./components/Transcript";
import { fetchClinic } from "./lib/api";
import { callReducer, initialCall } from "./lib/callState";
import type { Clinic } from "./lib/types";
import { VoiceClient } from "./lib/voiceClient";

// Development only: ?feed takes the caller's audio from recordings instead of the mic, so a headless browser can
// hold a whole call. Queue one with window.sofiaSay("/samples/booking-1.wav").
const FEED = import.meta.env.DEV && new URLSearchParams(location.search).has("feed");

declare global {
  interface Window {
    sofiaSay?: (url: string) => Promise<number>;
  }
}

export default function App() {
  const [call, dispatch] = useReducer(callReducer, initialCall);
  const [clinic, setClinic] = useState<Clinic>();
  const [clinicError, setClinicError] = useState<string>();
  const [allowInterrupt, setAllowInterrupt] = useState(true);
  const [level, setLevel] = useState(0);
  const [sofiaSpeaking, setSofiaSpeaking] = useState(false);
  const client = useRef<VoiceClient | null>(null);
  const interruptRef = useRef(allowInterrupt);
  interruptRef.current = allowInterrupt;

  useEffect(() => {
    let loaded = false;
    const load = () =>
      fetchClinic()
        .then((c) => {
          loaded = true;
          setClinic(c);
        })
        // A refresh that fails keeps the clinic shown; only the first load reports it
        .catch((err: Error) => loaded || setClinicError(err.message));
    void load();
    // The clinic's clock moves the calendar's now-line and "today", so it's refreshed every minute
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, []);

  const inCall = call.status === "connecting" || call.status === "live";

  // Whether Sofia is audible is a property of the audio clock, so it's polled
  useEffect(() => {
    if (!inCall) {
      setSofiaSpeaking(false);
      return;
    }
    const timer = setInterval(() => setSofiaSpeaking(client.current?.playing ?? false), 100);
    return () => clearInterval(timer);
  }, [inCall]);

  const stop = useCallback(() => client.current?.stop(), []);

  const start = useCallback(async () => {
    dispatch({ type: "connect" });
    const voice = new VoiceClient({
      feed: FEED,
      allowInterrupt: () => interruptRef.current,
      onEvent: (event, at) => dispatch({ type: "event", event, at }),
      onLevel: setLevel,
      onClose: (error) => {
        dispatch({ type: "closed", error });
        setLevel(0);
        if (client.current === voice) client.current = null;
        delete window.sofiaSay;
      },
    });
    client.current = voice;
    if (FEED) window.sofiaSay = (url) => voice.say(url);
    try {
      await voice.start();
    } catch (err) {
      const message = err instanceof DOMException && err.name === "NotAllowedError" ? "Microphone access was blocked" : String(err);
      voice.stop(message);
    }
  }, []);

  useEffect(() => () => client.current?.stop(), []);

  const last = call.turns.at(-1);
  const activity: Activity =
    call.status === "connecting" ? "connecting"
    : !inCall ? "idle"
    : call.callerSpeaking ? "caller"
    : sofiaSpeaking ? "sofia"
    : last?.status === "replying" ? "thinking"
    : "listening";

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4 text-slate-800 lg:p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">
            Sofia <span className="font-normal text-slate-500">· {clinic?.name ?? "Brightside Dental"}</span>
          </h1>
          <p className="text-sm text-slate-500">
            A voice receptionist for a dental clinic in {clinic?.city ?? "Austin, Texas"}. Talk to her, and watch the
            tools, timings and bookings as she works.
          </p>
        </div>
        <CallControls
          inCall={inCall}
          activity={activity}
          allowInterrupt={allowInterrupt}
          onAllowInterrupt={setAllowInterrupt}
          level={level}
          onStart={start}
          onStop={stop}
        />
      </header>

      {(call.error || clinicError) && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-800">
          {call.error ?? `Couldn't reach the API: ${clinicError}`}
        </div>
      )}
      {FEED && <div className="text-xs text-amber-700">Feed mode: the mic is off; audio comes from window.sofiaSay(url).</div>}

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <Transcript turns={call.turns} callerSpeaking={call.callerSpeaking} />
        </div>
        <div className="lg:col-span-4">
          <ToolTimeline turns={call.turns} />
        </div>
        <div className="lg:col-span-3">
          <LatencyPanel turns={call.turns} />
        </div>
      </div>

      {clinic && <Calendar clinic={clinic} change={call.change} />}
    </div>
  );
}
