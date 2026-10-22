"use strict";

// A read-only player for server-processed JSONL fixtures, not a microphone,
// ASR provider, LLM, TTS engine or payment authorization path.
(() => {
  const byId = id => document.getElementById(id);
  const root = document.querySelector(".voice-console");
  const play = byId("voice-replay");
  const next = byId("voice-next");
  const usePayment = byId("voice-use-payment");
  const scenarios = [...document.querySelectorAll("[data-voice-scenario]")];
  let data = null, scenario = "payment", requestVersion = 0;
  let cursor = -1, timer = null, running = false;
  const descriptions = {
    listen: "LISTEN / FIXTURE", hold: "HOLD / WAIT FOR MORE",
    commit_turn: "COMMIT / TURN COMPLETE", interrupt_tts: "BARGE-IN / STOP TTS",
    tts_started: "TTS START / EVENT ONLY", tts_finished: "TTS END / EVENT ONLY",
    ignore: "IGNORED EVENT",
  };

  // Fixed illustrative bars. They never purport to be measured microphone levels.
  for (let index = 0; index < 43; index++) {
    const bar = document.createElement("i");
    const height = 10 + Math.round(Math.abs(Math.sin(index * .83) * Math.sin(index * .21)) * 55);
    bar.style.setProperty("--bar-height", `${height}px`);
    bar.style.setProperty("--bar-delay", `${(index % 9) * -110}ms`);
    byId("voice-waveform").append(bar);
  }

  function motionOff() {
    return document.documentElement.dataset.motion === "off";
  }
  function updateControls() {
    const unavailable = !data;
    play.disabled = unavailable;
    next.disabled = unavailable || running;
    play.textContent = running ? "Pause replay" : cursor < 0 ? "Replay events" :
      cursor >= data.output_events.length - 1 ? "Replay again" : "Resume replay";
    next.textContent = data && cursor >= data.output_events.length - 1 ? "Restart steps" : "Next event";
    usePayment.hidden = !data?.payment_command;
    usePayment.disabled = unavailable || busy || !config;
    root.dataset.playing = String(running && !motionOff());
  }
  function stop() {
    clearTimeout(timer);
    timer = null;
    running = false;
    updateControls();
  }
  function paintEvent(index) {
    cursor = index;
    const event = data.output_events[index];
    const raw = data.input_events.find(item => item.t === event.t);
    root.dataset.voiceAction = event.action;
    byId("voice-state").textContent = descriptions[event.action] || event.action;
    byId("voice-clock").textContent = `${event.t.toFixed(2)}s`;
    byId("voice-transcript-label").textContent = event.action === "commit_turn" ? "COMMITTED UTTERANCE" :
      event.action === "tts_started" || event.action === "tts_finished" ? "SCRIPTED TTS TEXT / NO AUDIO" :
      `${raw?.type?.replaceAll("_", " ").toUpperCase() || "VOICE EVENT"} / NOT YET A PAYMENT`;
    byId("voice-transcript").textContent = event.text || "Silence; no new transcript.";
    byId("voice-reason").textContent = event.reason +
      (event.latency_ms ? ` · ${event.latency_ms}ms fixture silence` : "");
    [...byId("voice-events").children].forEach((item, position) => {
      item.classList.toggle("active", position === index);
      item.classList.toggle("seen", position <= index);
      if (position === index) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
    updateControls();
  }
  function schedule() {
    if (!running || !data) return;
    if (motionOff() || document.hidden || document.documentElement.dataset.view !== "payment") {
      stop();
      return;
    }
    const index = cursor + 1;
    if (index >= data.output_events.length) {
      stop();
      byId("voice-state").textContent = "REPLAY COMPLETE / NOT LIVE";
      byId("voice-reason").textContent = "Fixture replay finished. No audio played and no payment request was made.";
      return;
    }
    const previous = cursor < 0 ? 0 : data.output_events[cursor].t;
    // Small minimum dwell for readable labels, not reported as processing latency.
    const delay = Math.max(350, (data.output_events[index].t - previous) * 1000);
    timer = setTimeout(() => {
      if (!running) return;
      paintEvent(index);
      schedule();
    }, delay);
  }
  function step() {
    if (!data) return;
    stop();
    paintEvent(cursor >= data.output_events.length - 1 ? 0 : cursor + 1);
  }
  function renderEvidence() {
    const proof = byId("voice-proof");
    proof.replaceChildren();
    for (const [key, label] of [["voice_hash", "Voice-event hash"], ["intent_hash", "Intent hash"], ["mandate_hash", "Voice mandate hash"]]) {
      if (!data.evidence[key]) continue;
      const row = document.createElement("p");
      const title = document.createElement("span");
      title.textContent = label;
      const hash = document.createElement("code");
      hash.textContent = data.evidence[key];
      row.append(title, hash);
      proof.append(row);
    }
    const scope = document.createElement("p");
    scope.className = "voice-evidence-scope";
    scope.textContent = data.evidence.legacy_chain_id ?
      `Legacy fixture scope: chain ${data.evidence.legacy_chain_id}. These hashes belong only to this fixture, not to the payment reviewed below. No signature or identity proof.` :
      "Hashes cover this fixture's text/events, not raw audio, speaker identity or a new payment.";
    proof.append(scope);
    byId("voice-evidence-json").textContent = JSON.stringify({
      source: data.source,
      intent: data.intent,
      fixture_confirmation: data.confirmation,
      confirmation_reusable: false,
      execution_attempted: false,
      evidence: data.evidence,
    }, null, 2);
  }
  function renderSummary() {
    cursor = -1;
    root.dataset.voiceAction = "summary";
    byId("voice-state").textContent = "FIXTURE LOADED / NOT LIVE";
    byId("voice-clock").textContent = `${data.input_events.at(-1).t.toFixed(2)}s FIXTURE`;
    byId("voice-transcript-label").textContent = "COMMITTED TEXT / FIXTURE SUMMARY";
    byId("voice-transcript").textContent = data.payment_command ||
      data.committed_turns.at(-1) || "No complete turn was committed.";
    byId("voice-reason").textContent = `${data.committed_turns.length} committed turns · ${data.interrupts} TTS interruptions. Replay or step through the gateway decisions.`;
    byId("voice-intent").textContent = `${data.intent.action} · heuristic candidate`;
    byId("voice-source").textContent = data.source;
    byId("voice-events").replaceChildren();
    for (const event of data.output_events) {
      const row = document.createElement("li");
      const time = document.createElement("time");
      time.textContent = `${event.t.toFixed(2)}s`;
      const action = document.createElement("code");
      action.textContent = event.action;
      const reason = document.createElement("span");
      reason.textContent = event.reason;
      row.append(time, action, reason);
      byId("voice-events").append(row);
    }
    renderEvidence();
    if (!data.payment_command) {
      byId("voice-reason").textContent += " The legacy keyword classifier is unvalidated here; this scenario cannot be imported as a payment.";
    }
    updateControls();
  }
  async function loadScenario(name) {
    stop();
    const version = ++requestVersion;
    scenario = name;
    data = null;
    cursor = -1;
    updateControls();
    root.setAttribute("aria-busy", "true");
    byId("voice-state").textContent = "LOADING FIXTURE";
    byId("voice-clock").textContent = "—";
    byId("voice-transcript").textContent = "Reading local speech-event fixtures…";
    byId("voice-intent").textContent = "Waiting for fixture";
    byId("voice-evidence").open = false;
    byId("voice-source").textContent = "";
    byId("voice-proof").replaceChildren();
    byId("voice-evidence-json").textContent = "";
    byId("voice-events").replaceChildren();
    scenarios.forEach(button => {
      const selected = button.dataset.voiceScenario === name;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    try {
      const result = await jsonRequest(`/api/voice/demo?scenario=${encodeURIComponent(name)}`, {}, 10000);
      if (version !== requestVersion) return;
      if (result.source_type !== "local_jsonl_fixture_replay" || !result.output_events?.length) throw new Error("Invalid voice fixture response.");
      data = result;
      renderSummary();
    } catch (error) {
      if (version !== requestVersion) return;
      byId("voice-state").textContent = "FIXTURE UNAVAILABLE";
      byId("voice-transcript").textContent = "Could not load voice events.";
      byId("voice-reason").textContent = `${error.message} Select a scenario to retry. Typed payment review remains available.`;
    } finally {
      if (version === requestVersion) {
        root.setAttribute("aria-busy", "false");
        updateControls();
      }
    }
  }
  scenarios.forEach(button => button.addEventListener("click", () => loadScenario(button.dataset.voiceScenario)));
  play.addEventListener("click", () => {
    if (!data) return;
    if (running) { stop(); return; }
    if (motionOff()) { step(); return; }
    if (cursor >= data.output_events.length - 1) cursor = -1;
    running = true;
    if (cursor < 0) paintEvent(0);
    updateControls();
    schedule();
  });
  next.addEventListener("click", step);
  usePayment.addEventListener("click", () => {
    if (!data?.payment_command || busy || !config) return;
    stop();
    if (importVoicePayment(data.payment_command, data.source)) setToast("Committed text copied. Run a new payment review.");
  });
  document.addEventListener("payment-availability", updateControls);
  document.addEventListener("workspace-viewchange", stop);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });
  window.addEventListener("pagehide", stop);
  new MutationObserver(() => { if (motionOff()) stop(); }).observe(document.documentElement, {
    attributes: true, attributeFilter: ["data-motion"],
  });
  loadScenario(scenario);
})();
