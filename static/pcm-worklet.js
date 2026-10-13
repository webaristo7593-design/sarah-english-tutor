/* Stateful box-filter resampler: continuous mono PCM16 at 16 kHz. */
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.sum = 0;
    this.weight = 0;
    this.buffer = new Int16Array(512);
    this.index = 0;
    this.port.onmessage = (event) => {
      if (event.data?.type !== "flush") return;
      if (this.index) {
        const tail = this.buffer.slice(0, this.index);
        this.port.postMessage(tail.buffer, [tail.buffer]);
        this.index = 0;
      }
      this.port.postMessage({ type: "flushed" });
    };
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const value of channel) {
      let remaining = 1;
      while (remaining > 1e-8) {
        const take = Math.min(remaining, this.ratio - this.weight);
        this.sum += value * take;
        this.weight += take;
        remaining -= take;
        if (this.weight >= this.ratio - 1e-8) {
          const sample = Math.max(-1, Math.min(1, this.sum / this.weight));
          this.buffer[this.index++] = Math.round(
            sample * (sample < 0 ? 32768 : 32767),
          );
          this.sum = 0;
          this.weight = 0;
          if (this.index === this.buffer.length) {
            this.port.postMessage(this.buffer.buffer, [this.buffer.buffer]);
            this.buffer = new Int16Array(512);
            this.index = 0;
          }
        }
      }
    }
    // The output stays silent; connecting to destination keeps processing alive.
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
