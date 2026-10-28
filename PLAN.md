# Voice Receptionist Copilot: plan

A browser voice agent for a fictional Brazilian dental clinic (Clínica Sorriso Paulista, São Paulo). Callers talk in Portuguese or English; the agent answers questions about the clinic and books, reschedules or cancels appointments through tool calls, and can be interrupted mid-sentence.

## Stack (free)

| Part | Choice |
|---|---|
| Backend | Python, FastAPI, WebSockets |
| Speech-to-text | Groq `whisper-large-v3-turbo` (free tier, OpenAI-compatible) |
| LLM | Groq `openai/gpt-oss-120b` through the official `openai` SDK, with tool calling; `openai/gpt-oss-20b` when the first hits its daily free-tier cap |
| Text-to-speech | Kokoro-82M locally (pt-BR and English voices), `edge-tts` as a backup |
| Turn detection | Silero VAD on the server |
| Data | SQLite + SQLModel |
| Frontend | React + Vite + Tailwind |
| Hosting | Hugging Face Spaces (Docker, free CPU) for the API, Vercel for the UI |

Speech-to-text, the LLM and text-to-speech each sit behind a small interface. `LLM_PROVIDER=openai` switches to OpenAI's paid models; an OpenAI Realtime mode is an optional later comparison.

## Turn pipeline

1. The browser streams 16 kHz PCM mic audio to FastAPI over a WebSocket.
2. Silero VAD detects the end of speech; that utterance goes to Whisper.
3. The LLM streams a reply and calls tools along the way.
4. Each complete sentence goes to TTS right away, so audio starts before the reply is finished.
5. Speech during playback (barge-in) stops playback and cancels the reply.

Target: under 1.5 s from end of speech to first audio, logged per turn (STT, LLM first token, first audio).

## Tools

`check_availability`, `book_appointment`, `find_appointments`, `reschedule_appointment`, `cancel_appointment`, `confirm_action`, `get_clinic_info`, `handoff_to_human`.

Booking, rescheduling and cancelling only propose a change. `confirm_action` carries it out, and the server accepts it only on a later caller turn than the proposal. In live tests the model sometimes booked as soon as the caller picked a time, even when the prompt told it to wait, so this rule lives in code rather than the prompt.

## Milestones

1. **Text-only core**: FastAPI, tools, SQLite, the streaming agent loop, a terminal chat, pytest with scripted conversations.
2. **Voice** (done): WebSocket audio, VAD, Whisper, Kokoro, sentence-by-sentence speech. The first live turn took 4.8 s to first audio: STT 0.7 s, then 2.4 s for the LLM's first text and 1.8 s to synthesize a long first sentence.
3. **Barge-in and timing** (done): cancel replies mid-stream and keep only what the caller heard, per-turn latency logs, a filler while tools run, a short first piece. Live, first audio now comes 1.7-2.0 s after the end of speech (it was 4.8 s), and the first words of the actual reply about 1 s later on turns with tools. Not counted in those numbers: the 600 ms of silence that tells us the caller is done. Still open: a faster model for turns without tools, and the 20b fallback's 8K tokens-per-minute cap, which adds seconds once 120b's daily quota is gone.
4. **Frontend**: mic, live transcript, tool timeline, latency numbers, bookings calendar.
5. **Bilingual and clinic info**: PT/EN detection and voices, retrieval over clinic docs if they outgrow topic lookup.
6. **Deploy and write-up**: Spaces + Vercel, README with GIF, Medium article, LinkedIn project.
