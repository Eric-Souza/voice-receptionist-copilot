// Runs on the audio thread. Downsamples the mic to 16 kHz (averaging each output sample's inputs, a cheap
// low-pass against aliasing) and posts ~100 ms Int16 chunks with their loudness for the level meter.
class Mic extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const rate = options.processorOptions.targetRate;
    this.ratio = sampleRate / rate;
    this.chunk = new Int16Array(rate / 10);
    this.filled = 0;
    this.phase = 0;
    this.acc = 0;
    this.count = 0;
    this.energy = 0;
  }

  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.acc += input[i];
      this.count++;
      if (++this.phase < this.ratio) continue;
      this.phase -= this.ratio;
      const s = Math.max(-1, Math.min(1, this.acc / this.count));
      this.acc = this.count = 0;
      this.energy += s * s;
      this.chunk[this.filled++] = s * 32767;
      if (this.filled === this.chunk.length) {
        const pcm = this.chunk.buffer;
        this.port.postMessage({ pcm, level: Math.sqrt(this.energy / this.filled) }, [pcm]);
        this.chunk = new Int16Array(this.chunk.length);
        this.filled = 0;
        this.energy = 0;
      }
    }
    return true;
  }
}

registerProcessor("mic", Mic);
