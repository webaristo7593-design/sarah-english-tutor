// PCM resampling must preserve timing and DC level across browser render blocks.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync(
  require("node:path").join(__dirname, "static/pcm-worklet.js"),
  "utf8",
);
for (const sampleRate of [16000, 24000, 44100, 48000]) {
  let Capture;
  const chunks = [];
  let flushed = false;
  class Processor {
    constructor() {
      this.port = {
        postMessage: (buffer) => {
          if (buffer?.type === "flushed") flushed = true;
          else chunks.push(new Int16Array(buffer));
        },
      };
    }
  }
  vm.runInNewContext(code, {
    sampleRate,
    AudioWorkletProcessor: Processor,
    registerProcessor: (_, type) => (Capture = type),
    Int16Array,
    Math,
  });
  const capture = new Capture();
  for (let offset = 0; offset < sampleRate; offset += 128) {
    const count = Math.min(128, sampleRate - offset);
    assert.equal(capture.process([[new Float32Array(count).fill(0.5)]]), true);
  }
  assert.equal(chunks.length, 31);
  assert.equal(chunks.flatMap((c) => Array.from(c)).length, 15872);
  assert.equal(capture.index, 128); // exactly 16000 samples, including the partial packet
  for (const chunk of chunks)
    for (const value of chunk) assert.equal(value, 16384);
  capture.port.onmessage({ data: { type: "flush" } });
  assert.equal(flushed, true);
  assert.equal(capture.index, 0);
  assert.equal(chunks.at(-1).length, 128);
  assert.equal(
    chunks.reduce((total, chunk) => total + chunk.length, 0),
    16000,
  );
  console.log(
    `PASS: ${sampleRate} Hz -> 16000 Hz; exact sample count across 128-frame blocks`,
  );
}
