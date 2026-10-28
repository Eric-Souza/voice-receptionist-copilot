# Voice Receptionist Copilot

A voice receptionist for a fictional dental clinic in Austin, Texas, Brightside Dental. The agent, Sofia, answers questions about the clinic and books, reschedules or cancels appointments through tool calls.

**Status:** milestone 4 of 6. The agent loop, tools, scheduling, SQLite, the HTTP API and a terminal chat came first, then voice: a WebSocket that takes mic audio and answers out loud. Then barge-in, so the caller can interrupt Sofia mid-sentence, and per-turn latency logs. Now there's a React web app: you talk to Sofia and watch her transcript, tool calls, latency and the clinic's bookings calendar live. Clinic-info polish and deployment come next; see [PLAN.md](PLAN.md).

The whole stack runs on free tiers: Groq for the LLM and Whisper, with Silero VAD and Kokoro text-to-speech running locally on the CPU. Setting `LLM_PROVIDER=openai` switches to OpenAI's paid models.

## Run it

You'll need Python 3.12+, [uv](https://docs.astral.sh/uv/), and a free [Groq API key](https://console.groq.com/keys).

```bash
cp .env.example .env          # then fill in GROQ_API_KEY
cd apps/api
uv venv && uv pip install -e ".[dev]"

.venv/Scripts/python -m app.cli        # chat in the terminal
```

On macOS or Linux, use `.venv/bin/python` instead. The first run creates `data/clinic.db` and fills about a third of the next two weeks' slots with demo patients. Run `python -m app.seed --reset` to start over.

The terminal chat shows each tool call and its result, dimmed:

```
You: Hi, I'd like a cleaning tomorrow morning.
  -> check_availability({"date": "2026-10-29", "days": 1, "part_of_day": "morning", "service": "cleaning"})
Sofia: We have 8 am with Dr. Reed, or 9 am with Dr. Reed or Dr. Mendez. Which one works for you?
You: The first one. Maria Lopez, 512 555 0177.
  -> book_appointment({...})            <- {"pending": {"action_id": "A1", ...}}
Sofia: So that's a cleaning with Dr. Reed on Thursday the 29th at 8 am, correct?
You: Yes.
  -> confirm_action({"action_id": "A1"}) <- {"booked": {...}}
Sofia: Your cleaning is booked for Thursday the 29th at 8 am. Have a great day!
```

### Voice

```bash
.venv/Scripts/python -m app.voice.download   # Silero VAD and Kokoro, about 120 MB, into apps/api/models
.venv/Scripts/python -m uvicorn app.main:app
```

Open http://localhost:8000/voice and start the call. The page shows what Whisper heard, Sofia's reply, her tool calls and each turn's timings. Talk over her to interrupt. If she keeps interrupting herself because her voice leaks from the speakers into the mic, use headphones or untick "Let me interrupt".

A run from synthesized caller speech streamed in at real-time pace, with real VAD, Whisper and Kokoro and the model's replies scripted. The caller asks about parking and prices, then talks over the answer:

```
12.7s  vad          caller stopped talking
13.3s  transcript   "Hi, do you have parking and how much is a cleaning?"
14.0s  tool_call    get_clinic_info({"topic": "parking"})
14.0s  tool_call    get_clinic_info({"topic": "payment"})
14.2s  audio        "One moment."                                         (filler)
15.3s  audio        "There's free parking in the garage behind the building,"
15.7s  audio        "on levels 2 and 3."
16.1s  audio        "Street parking on South Lamar is metered."
16.6s  vad          caller starts talking over Sofia
16.6s  audio        "And a cleaning is $120."
16.8s  interrupt    heard: "There's free parking in the garage behind the building,"
20.8s  transcript   "Sorry, actually, are you open on Saturdays?"
22.3s  audio        "Yes, we're open on Saturdays from 8 am to noon."
```

Each turn is also appended to `data/voice-turns.jsonl` with its timings, the tools it called, and whether it was interrupted.

### Web app

With the API running on port 8000, start the frontend (Node 20.19+):

```bash
cd apps/web
npx npm@11 install   # npm 10.8 crashes on vitest 5's optional peers ("reading 'edgesOut'")
npm run dev          # http://localhost:5173, proxies /api and /ws to :8000
```

The page has four panels:
- **Transcript:** the caller's words as Whisper heard them, and Sofia's reply as it streams. When you cut her off, the part you didn't hear is struck through.
- **Tool calls:** each tool with its arguments and result, timed from the end of your speech, with a marker for when the first audio went out.
- **Latency:** for each turn, a bar split into speech-to-text, time to first audio, and the rest of the reply. Medians on top.
- **Bookings:** the week's appointments, one lane per dentist, with lunch and closed hours shaded. When Sofia confirms a booking, a move or a cancellation, the calendar jumps to that week, reloads and highlights the appointment.

`npm test` runs the unit tests (vitest) for the reducer that turns the WebSocket events into the call state. `npm run build` typechecks and builds into `dist/`. For a deployed build, set `VITE_API_URL` to the API's origin, and the API's `CORS_ORIGINS` to the web app's (it allows only `http://localhost:5173` by default).

To run a call without a mic (in a headless browser, say), open `http://localhost:5173/?feed` in dev mode. Then queue recordings from the console with `await sofiaSay("/samples/en-1.wav")`. They're streamed in at real-time pace, with silence in between. `public/samples/` is gitignored; make clips with any TTS (I used Kokoro with a different voice than Sofia's).

### HTTP API

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

| Endpoint | What it does |
|---|---|
| `POST /api/sessions` | Starts a conversation and returns `session_id` and the greeting |
| `POST /api/sessions/{id}/messages` `{"text": "..."}` | One caller turn; returns the reply plus the tool calls and results |
| `GET /api/appointments?start=YYYY-MM-DD&days=7` | Booked appointments, for the calendar view |
| `GET /api/clinic` | Name, city, services and prices, dentists, opening hours, slot length and the clinic's current time |
| `GET /health` | Liveness check |
| `WS /ws/voice` | A voice call; the protocol and barge-in are documented in `app/voice/call.py` |
| `GET /voice` | The browser test page for it |

### Tests

```bash
.venv/Scripts/python -m pytest                                  # offline: a scripted fake model, fake STT and TTS
LIVE_GROQ_API_KEY=gsk_... .venv/Scripts/python -m pytest -m live   # real model, uses your Groq quota
```

## How it works

Each caller turn runs a loop (`app/agent.py`):
1. Stream the model's reply.
2. Run any tool calls against SQLite.
3. Feed the results back to the model, and repeat until it just talks.

Text deltas are yielded as they arrive, so the voice layer can start speaking the first sentence early.

Design choices, mostly prompted by live testing:

- **Confirmation is enforced in code.** `book_appointment`, `reschedule_appointment` and `cancel_appointment` only validate and *propose* a change. `confirm_action` carries it out, and only on a later caller turn than the proposal, after the caller has heard the read-back. Prompting alone didn't work: the model sometimes booked as soon as the caller picked a time.
- **Tools return ready-to-speak times.** Every slot and appointment comes with `say: "Thursday the 29th at 8 am"`. Given only ISO times, the model sometimes read them out as "13:00" or "2026-10-29".
- **Errors go back to the model.** Unknown services, taken slots and malformed arguments come back as `{"error": ...}`, so Sofia can apologize and offer something else.
- **Free-tier resilience.** Groq caps tokens per day per model, and one booking call uses roughly 30–40K tokens. When `gpt-oss-120b` is rate limited, the client falls back to `gpt-oss-20b` and skips 120b until the 429's retry-after has passed, instead of asking it again (and waiting out a retry) on every call. A model call that fails before any output is retried once, and so is an empty reply.
- **Speech is sent sentence by sentence.** As the reply streams in, each complete sentence goes to Kokoro and out to the browser while the model is still writing the next one. Before synthesis, "Dr." becomes "Doctor" and `$1,200.50` becomes "1200 dollars and 50 cents", because Kokoro read them as "draw" and stumbled over the digits.
- **The caller can interrupt.** The server keeps listening while Sofia thinks and talks. Once the caller has talked over her for 300 ms, the reply is cancelled and the browser drops the audio it queued; shorter sounds like "uh-huh" don't count. The server knows how long each sentence plays, so it can tell which ones the caller heard. The history then keeps only those, ending in "…", so the model knows where it was cut off. A change proposed in the cut-off reply is withdrawn, since the caller may not have heard it read back. A tool that was running finishes and records its result first, so the history always matches the database. If the caller just paused and went on before Sofia said anything, both parts go to Whisper together as one utterance.
- **Silence is filled, and the first piece is short.** If the model goes to a tool before saying anything, Sofia says "One moment" while it runs. The first spoken piece of a reply is cut at a comma when the sentence runs long, because synthesis time grows with length.
- **Whisper's noise is filtered out.** Segments it marks as probably not speech are dropped, and so are its usual subtitle hallucinations ("Thanks for watching!", "Subtitles by the Amara.org community").
- **Clinic facts live in [data/clinic-info.md](data/clinic-info.md).** Each `## topic` section is one `get_clinic_info` answer.

## Layout

```
PLAN.md                 stack, pipeline and milestones
data/clinic-info.md     clinic facts for get_clinic_info
apps/api/app/
  agent.py              the turn loop
  llm.py                streaming OpenAI-compatible client, with model fallback
  tools.py              tool schemas and implementations
  scheduling.py         free slots and booking rules
  clinic.py             services, dentists, opening hours
  prompts.py            Sofia's system prompt
  routers/chat.py       HTTP API
  routers/voice.py      voice WebSocket and test page
  voice/                VAD, Whisper, Kokoro, speech text rewrites and the call loop
  static/voice.html     browser test page
  cli.py, seed.py       terminal chat, demo data
apps/api/tests/         pytest, with a scripted fake model and fake STT and TTS
apps/web/               React 19 + Vite + Tailwind
  public/mic-worklet.js   downsamples the mic to 16 kHz on the audio thread
  src/lib/voiceClient.ts  WebSocket, mic or feed input, sentence playback
  src/lib/callState.ts    reducer from WebSocket events to the call state
  src/components/         transcript, tool timeline, latency panel, calendar
```
