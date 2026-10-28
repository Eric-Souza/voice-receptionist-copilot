# Voice Receptionist Copilot

A voice receptionist for a fictional dental clinic in São Paulo, Clínica Sorriso Paulista. Callers speak Portuguese or English. The agent, Sofia, answers questions about the clinic and books, reschedules or cancels appointments through tool calls.

**Status:** milestone 2 of 6. The agent loop, tools, scheduling, SQLite, the HTTP API and a terminal chat came first. Voice is now in place: a WebSocket that takes mic audio and answers out loud, plus a bare browser page to try it. Barge-in and the real frontend come next; see [PLAN.md](PLAN.md).

The whole stack runs on free tiers: Groq for the LLM and Whisper, with Silero VAD and Kokoro text-to-speech running locally on the CPU. Setting `LLM_PROVIDER=openai` switches to OpenAI's paid models.

## Run it

You'll need Python 3.12+, [uv](https://docs.astral.sh/uv/), and a free [Groq API key](https://console.groq.com/keys).

```bash
cp .env.example .env          # then fill in GROQ_API_KEY
cd apps/api
uv venv && uv pip install -e ".[dev]"

.venv/Scripts/python -m app.cli        # chat in the terminal (Portuguese greeting)
.venv/Scripts/python -m app.cli --en   # English greeting
```

On macOS or Linux, use `.venv/bin/python` instead. The first run creates `data/clinic.db` and fills about a third of the next two weeks' slots with demo patients. Run `python -m app.seed --reset` to start over.

The terminal chat shows each tool call and its result, dimmed:

```
You: Hi, I'd like a cleaning tomorrow morning.
  -> check_availability({"date": "2026-10-29", "days": 1, "part_of_day": "morning", "service": "cleaning"})
Sofia: We have 8 am with Dra. Ana, or 9 am with Dra. Ana or Dra. Carla. Which one works for you?
You: The first one. Maria Souza, 11 98888-7777.
  -> book_appointment({...})            <- {"pending": {"action_id": "A1", ...}}
Sofia: So that's a cleaning with Dra. Ana on Thursday the 29th at 8 am, correct?
You: Yes.
  -> confirm_action({"action_id": "A1"}) <- {"booked": {...}}
Sofia: Your cleaning is booked for Thursday the 29th at 8 am. Have a great day!
```

### Voice

```bash
.venv/Scripts/python -m app.voice.download   # Silero VAD and Kokoro, about 120 MB, into apps/api/models
.venv/Scripts/python -m uvicorn app.main:app
```

Open http://localhost:8000/voice, pick a language and start the call. The page shows what Whisper heard, Sofia's reply, her tool calls and each turn's timings. For now it's half duplex: wait for Sofia to finish before you talk. Headphones help.

A live run, from a synthesized Portuguese question about parking and prices:

```
transcript   "Oi, vocês têm estacionamento e quanto custa uma limpeza?"   [pt]
tool_call    get_clinic_info({"topic": "parking"})
audio        "Tem estacionamento com manobrista pago, R$ 25 por até duas horas, ..."
audio        "A limpeza custa R$ 180."
audio        "Posso agendar sua limpeza?"
turn_done    stt 679 ms · LLM first text 3056 ms · first audio 4843 ms · total 5701 ms
```

### HTTP API

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

| Endpoint | What it does |
|---|---|
| `POST /api/sessions` `{"language": "pt" \| "en"}` | Starts a conversation and returns `session_id` and the greeting |
| `POST /api/sessions/{id}/messages` `{"text": "..."}` | One caller turn; returns the reply plus the tool calls and results |
| `GET /api/appointments?start=YYYY-MM-DD&days=7` | Booked appointments, for the calendar view |
| `GET /api/clinic` | Services, dentists and opening hours |
| `GET /health` | Liveness check |
| `WS /ws/voice?language=pt\|en` | A voice call; the protocol is documented in `app/voice/call.py` |
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
- **Tools return ready-to-speak times.** Every slot and appointment comes with `say: {"en": "Thursday the 29th at 8 am", "pt": "quinta, dia 29, às 8h"}`. Given only ISO times, the model said "13h" in English.
- **Errors go back to the model.** Unknown services, taken slots and malformed arguments come back as `{"error": ...}`, so Sofia can apologize and offer something else.
- **Free-tier resilience.** Groq caps tokens per day per model, and one booking call uses roughly 30–40K tokens. When `gpt-oss-120b` is rate limited, the client falls back to `gpt-oss-20b`. A model call that fails before any output is retried once, and so is an empty reply.
- **Speech is sent sentence by sentence.** As the reply streams in, each complete sentence goes to Kokoro and out to the browser while the model is still writing the next one. Before synthesis, `R$ 180` becomes "180 reais", `8h30` becomes "8 e meia" and "Dra." becomes "Doutora", because Kokoro read them as "real dollar", "oito H30" and "draw".
- **The caller can switch language.** Whisper detects the language of each utterance, and anything of three words or more sets the language Sofia answers and speaks in. Short ones like "sim" or "OK" keep the current language, since Whisper's guess on them is unreliable.
- **Whisper's noise is filtered out.** Segments it marks as probably not speech are dropped, and so are its usual subtitle hallucinations ("Legendas pela comunidade Amara.org").
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
```
