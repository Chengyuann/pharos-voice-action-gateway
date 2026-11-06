"use strict";

(() => {
  const el = id => document.getElementById(id);
  let context, stream, capture, chunks = [], epoch = 0, timer, controller;
  let sampleRate = 16000, recording = false, startedAt = 0, speechAt = 0;
  let utterance, config, hasSpeech = false, opening = false;
  const state = text => { el("mic-status").textContent = text; };
  function controls() {
    el("mic-start").disabled = recording || opening || !config?.available;
    el("mic-stop").disabled = !recording;
    el("mic-cancel").disabled = !recording && !opening && !controller && !utterance && !el("mic-text").value;
    el("mic-use").disabled = recording || !el("mic-text").value.trim();
  }
  function stopSpeech() {
    window.speechSynthesis?.cancel();
    utterance = null;
    controls();
  }
  async function closeCapture() {
    clearTimeout(timer);
    recording = false;
    stream?.getTracks().forEach(track => track.stop());
    capture?.disconnect();
    if (context && context.state !== "closed") await context.close();
    stream = context = capture = null;
    controls();
  }
  function wav(samples, rate) {
    const size = Math.floor(samples.length * 16000 / rate);
    const data = new ArrayBuffer(44 + size * 2), view = new DataView(data);
    const write = (at, text) => [...text].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
    write(0, "RIFF"); view.setUint32(4, 36 + size * 2, true); write(8, "WAVE");
    write(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, "data");
    view.setUint32(40, size * 2, true);
    for (let i = 0; i < size; i++) {
      const from = Math.floor(i * rate / 16000), to = Math.max(from + 1, Math.floor((i + 1) * rate / 16000));
      let value = 0;
      for (let j = from; j < to && j < samples.length; j++) value += samples[j];
      value = Math.min(1, Math.max(-1, value / (to - from)));
      view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    }
    return new Blob([data], {type: "audio/wav"});
  }
  async function finish() {
    if (!recording) return;
    const version = epoch, rate = sampleRate, segments = chunks;
    chunks = [];
    await closeCapture();
    if (version !== epoch) return;
    const samples = new Float32Array(segments.reduce((sum, chunk) => sum + chunk.length, 0));
    let offset = 0;
    segments.forEach(chunk => { samples.set(chunk, offset); offset += chunk.length; });
    if (!hasSpeech || samples.length / rate < .25) { state("No speech detected. Try again."); return; }
    const request = new AbortController();
    controller = request;
    const timeout = setTimeout(() => request.abort(), 95000);
    state("Transcribing on this computer…"); controls();
    try {
      const response = await fetch("/api/voice/transcribe", {
        method: "POST", body: wav(samples, rate), signal: request.signal,
        headers: {"Content-Type": "audio/wav"},
      });
      const result = await response.json();
      if (epoch !== version) return;
      if (!response.ok) throw new Error(result.error || "Transcription failed.");
      el("mic-text").value = result.text;
      state(result.text ? `Check the transcript · ${(result.processing_ms / 1000).toFixed(1)}s processing` : "No clear speech found.");
    } catch (error) {
      if (epoch === version) state(error.name === "AbortError" ? "Transcription stopped." : error.message);
    } finally {
      clearTimeout(timeout);
      if (controller === request) controller = null;
      controls();
    }
  }
  async function start() {
    if (recording || opening || !config?.available) return;
    opening = true;
    const version = ++epoch;
    controller?.abort(); controller = null;
    stopSpeech();
    // New speech cannot keep an old payment approval alive.
    invalidate("New voice input started. Review the new instruction before continuing.");
    chunks = []; hasSpeech = false;
    el("mic-text").value = "";
    state("Waiting for microphone permission…");
    controls();
    try {
      const granted = await navigator.mediaDevices.getUserMedia({audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true,
      }});
      if (version !== epoch) { granted.getTracks().forEach(t => t.stop()); return; }
      stream = granted;
      context = new AudioContext();
      sampleRate = context.sampleRate;
      await context.audioWorklet.addModule("/assets/voice-capture.js");
      if (version !== epoch) { await closeCapture(); return; }
      capture = new AudioWorkletNode(context, "voice-capture");
      const input = context.createMediaStreamSource(stream), muted = context.createGain();
      muted.gain.value = 0;
      input.connect(capture); capture.connect(muted); muted.connect(context.destination);
      startedAt = performance.now(); speechAt = startedAt; recording = true;
      capture.port.onmessage = ({data}) => {
        if (!recording || version !== epoch) return;
        chunks.push(data);
        const now = performance.now();
        const energy = Math.sqrt(data.reduce((n, v) => n + v * v, 0) / data.length);
        if (energy > .012) { hasSpeech = true; speechAt = now; }
        state(`Listening · ${((now - startedAt) / 1000).toFixed(1)} / 20s`);
        if (now - startedAt >= 19800 || (el("mic-auto").checked && hasSpeech && now - speechAt > 1600)) finish();
      };
      timer = setTimeout(finish, 19800);
      controls();
    } catch (error) {
      await closeCapture();
      state(error.name === "NotAllowedError" ? "Microphone permission was not granted. Typed input still works." : error.message);
    } finally {
      opening = false;
      controls();
    }
  }
  function cancel() {
    ++epoch;
    controller?.abort(); controller = null;
    chunks = []; el("mic-text").value = "";
    stopSpeech(); closeCapture();
    invalidate("Voice input cancelled. Previous approval is no longer valid.");
    state("Cancelled. Nothing submitted.");
  }
  el("mic-start").addEventListener("click", start);
  el("mic-stop").addEventListener("click", finish);
  el("mic-cancel").addEventListener("click", cancel);
  el("mic-text").addEventListener("input", controls);
  el("mic-use").addEventListener("click", () => {
    if (recording || !el("mic-text").value.trim()) return;
    const text = el("mic-text").value.trim();
    if (/^(?:stop|cancel|cancel (?:it|payment)|never mind)[.!]?$/i.test(text)) {
      cancelCurrentAction(); state("Payment cancelled."); return;
    }
    $("request").value = text;
    $("request").dispatchEvent(new Event("input", {bubbles:true}));
    state("Transcript copied. Review payment when ready.");
  });
  el("voice-read").addEventListener("click", () => {
    stopSpeech();
    if (!window.speechSynthesis) { state("Speech playback is unavailable in this browser."); return; }
    const local = speechSynthesis.getVoices().find(v => v.localService && /^en[-_]/i.test(v.lang));
    if (!local) { state("No local English voice found. The review remains available as text."); return; }
    utterance = new SpeechSynthesisUtterance($("agent-response-text").textContent.slice(0, 800));
    utterance.voice = local; utterance.lang = "en-US"; utterance.rate = 1;
    utterance.onend = () => { utterance = null; controls(); };
    speechSynthesis.speak(utterance); controls();
  });
  el("voice-stop").addEventListener("click", stopSpeech);
  document.addEventListener("workspace-viewchange", () => { if (opening || recording || controller || utterance) cancel(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && (opening || recording || controller || utterance)) cancel(); });
  window.addEventListener("pagehide", cancel);
  fetch("/api/voice/config").then(r => r.json()).then(value => {
    config = value; state(value.message); controls();
  }).catch(() => { state("Local voice service unavailable. Use typed input."); controls(); });
})();
