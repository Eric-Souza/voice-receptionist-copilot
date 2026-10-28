// The browser end of a call: streams 16 kHz mic audio to /ws/voice and plays Sofia's replies back, sentence after
// sentence. Audio goes up all the time so the server can hear the caller talk over her.

import { voiceUrl } from "./api";
import type { Language, ServerEvent } from "./types";

const MIC_RATE = 16_000;
const CHUNK = MIC_RATE / 10; // 100 ms
// With interruptions off the mic is muted while Sofia talks, plus this long for the echo to die down
const ECHO_TAIL_S = 0.3;

export interface VoiceClientOptions {
  language: Language;
  onEvent: (event: ServerEvent, at: number) => void;
  onClose: (error?: string) => void;
  onLevel?: (level: number) => void;
  allowInterrupt: () => boolean;
  // Development: no mic, audio comes from say(url) instead (for tests in a headless browser)
  feed?: boolean;
}

/** What goes up the socket: the real mic, or recorded clips with silence in between. */
interface Input {
  stop(): void;
}

export class VoiceClient {
  private ws?: WebSocket;
  private playCtx?: AudioContext;
  private input?: Input;
  private feedQueue: Int16Array[] = [];
  private sources: AudioBufferSourceNode[] = [];
  private playEnd = 0;
  private sampleRate = 24_000;
  private closed = false;
  private options: VoiceClientOptions;

  constructor(options: VoiceClientOptions) {
    this.options = options;
  }

  /** Whether Sofia's voice is coming out of the speakers right now. */
  get playing(): boolean {
    return !!this.playCtx && this.playCtx.currentTime < this.playEnd;
  }

  async start(): Promise<void> {
    // Created in the click handler, so the browser lets it play
    this.playCtx = new AudioContext();
    this.input = this.options.feed ? this.feedInput() : await this.micInput();
    const ws = new WebSocket(voiceUrl(this.options.language));
    ws.binaryType = "arraybuffer";
    ws.onmessage = (m) => {
      if (typeof m.data === "string") {
        const event = JSON.parse(m.data) as ServerEvent;
        if (event.type === "ready") this.sampleRate = event.sample_rate;
        if (event.type === "interrupt") this.stopPlayback();
        this.options.onEvent(event, performance.now());
      } else {
        this.play(m.data as ArrayBuffer);
      }
    };
    ws.onerror = () => this.stop("Couldn't reach the voice server");
    ws.onclose = (e) => this.stop(e.code === 1000 || e.code === 1005 ? undefined : `Connection closed (${e.code})`);
    this.ws = ws;
  }

  stop(error?: string): void {
    if (this.closed) return;
    this.closed = true;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close();
    this.input?.stop();
    this.stopPlayback();
    void this.playCtx?.close();
    this.options.onClose(error);
  }

  /** Feed mode: queue a recording (any format the browser decodes) as if the caller said it. */
  async say(url: string): Promise<number> {
    const data = await (await fetch(url)).arrayBuffer();
    // Decoding in a 16 kHz context resamples to the rate the server wants
    const decoded = await new OfflineAudioContext(1, 1, MIC_RATE).decodeAudioData(data);
    const samples = decoded.getChannelData(0);
    const pcm = Int16Array.from(samples, (s) => Math.max(-1, Math.min(1, s)) * 32767);
    for (let i = 0; i < pcm.length; i += CHUNK) this.feedQueue.push(pcm.slice(i, i + CHUNK));
    return decoded.duration;
  }

  private send(pcm: ArrayBuffer): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const muted = !this.options.allowInterrupt() && this.playCtx!.currentTime < this.playEnd + ECHO_TAIL_S;
    // Silence rather than nothing, so the server's clock stays in step with the caller's
    this.ws.send(muted ? new ArrayBuffer(pcm.byteLength) : pcm);
  }

  private async micInput(): Promise<Input> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    const ctx = new AudioContext();
    try {
      await ctx.audioWorklet.addModule(`${import.meta.env.BASE_URL}mic-worklet.js`);
    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      void ctx.close();
      throw err;
    }
    const node = new AudioWorkletNode(ctx, "mic", { processorOptions: { targetRate: MIC_RATE } });
    node.port.onmessage = (m: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
      this.options.onLevel?.(m.data.level);
      this.send(m.data.pcm);
    };
    ctx.createMediaStreamSource(stream).connect(node);
    return {
      stop: () => {
        stream.getTracks().forEach((t) => t.stop());
        node.port.onmessage = null;
        void ctx.close();
      },
    };
  }

  private feedInput(): Input {
    // Paced in real time like a mic: the server's turn-taking depends on how long silences last
    const began = performance.now();
    let sent = 0;
    const timer = setInterval(() => {
      const due = Math.floor((performance.now() - began) / 100);
      for (; sent < due; sent++) {
        const chunk = this.feedQueue.shift() ?? new Int16Array(CHUNK);
        let energy = 0;
        for (const s of chunk) energy += (s / 32768) ** 2;
        this.options.onLevel?.(Math.sqrt(energy / chunk.length));
        this.send(chunk.buffer as ArrayBuffer);
      }
    }, 50);
    return { stop: () => clearInterval(timer) };
  }

  private play(bytes: ArrayBuffer): void {
    const ctx = this.playCtx;
    if (!ctx || bytes.byteLength < 2) return;
    const pcm = new Int16Array(bytes);
    const buffer = ctx.createBuffer(1, pcm.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    const at = Math.max(ctx.currentTime + 0.05, this.playEnd);
    source.start(at);
    this.playEnd = at + buffer.duration;
    this.sources.push(source);
    source.onended = () => {
      this.sources = this.sources.filter((s) => s !== source);
    };
  }

  private stopPlayback(): void {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // already stopped
      }
    }
    this.sources = [];
    this.playEnd = 0;
  }
}
