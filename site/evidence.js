"use strict";
const tasks = {
  policy: {
    video: "01-policy-gatekeeper-mcy",
    title: "Policy Gatekeeper", kind: "DETERMINISTIC MOCK REPLAY",
    description: "Spend limit, allowlist and time window. Each denial records the deciding rule and leaves the mock executor uncalled.",
    scenarios: [["Allow", "policy-allow"], ["Deny amount", "policy-deny-amount"],
      ["Deny recipient", "policy-deny-recipient"], ["Deny time", "policy-deny-time"]],
  },
  payment: {
    video: "02-payment-agent-mcy",
    title: "Natural-language payment", kind: "RECORDED MAINNET READS",
    description: "One bounded English payment, explicit clarification, exact token units, on-chain validation, and a human-reviewed unsigned handoff in the local workspace.",
    scenarios: [["Clarify amount", "language-demo"], ["PROS · latest · allow", "pros-20261022-live"],
      ["PROS · Oct 21", "pros-allow-live"],
      ["WPROS · allow", "wpros-allow-live"], ["USDC · insufficient balance", "usdc-deny-live"]],
  },
  composition: {
    video: "03-skill-chain-mcp-mcy",
    title: "Skill + Chain MCP", kind: "RECORDED COMPOSED FLOW",
    description: "The local payment skill consumes actual Chain MCP results. Changing the returned balance, simulation result or gas quote changes its decision.",
    scenarios: [["Native transfer · latest", "pros-20261022-live"], ["ERC-20 transfer", "wpros-allow-live"],
      ["Fail closed", "usdc-deny-live"]],
  },
};
const $ = id => document.getElementById(id);
let generation = 0;
function node(tag, text, className = "") {
  const el = document.createElement(tag); el.textContent = text; el.className = className; return el;
}
function fact(name, value) { $("facts").append(node("dt", name), node("dd", String(value))); }
function check(name, passed, detail) {
  const li = node("li", "");
  li.append(node("strong", `${passed ? "✓" : "!"} ${name}`, passed ? "pass" : "fail"),
    node("small", detail)); $("checks").append(li);
}
async function selectScenario(task, name, button) {
  const revision = ++generation;
  for (const b of $("scenarios").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === button));
  $("loading").textContent = "Loading recorded evidence…";
  for (const id of ["facts", "checks", "trace", "raw"]) $(id).replaceChildren();
  $("result-heading").textContent = "—"; $("boundary").textContent = "";
  $("download").hidden = true;
  try {
    const response = await fetch(`proofs/${name}.json`, {cache:"no-store"});
    if (!response.ok) throw new Error(`Evidence unavailable (${response.status})`);
    let data = await response.json();
    if (revision !== generation) return;
    $("raw").textContent = JSON.stringify(data, null, 2);
    $("download").href = `proofs/${name}.json`; $("download").hidden = false;
    const decision = data.decision || data.policy_decision;
    $("result-heading").textContent = decision ? decision.outcome.toUpperCase() : "CLARIFY";
    $("result-heading").className = decision?.outcome === "deny" ? "deny" : "";
    if (name === "language-demo") {
      fact("Original instruction", data.request);
      fact("Follow-up", data.first.question);
      fact("Explicit answer", data.answer.amount);
      fact("Resolved payment", JSON.stringify(data.resolved.payment));
      $("boundary").textContent = "Recorded parser output. This clarification did not sign, send, or check a chain balance.";
      check("Ambiguity detected", data.first.status === "needs_details", data.first.reason);
      check("No guessed amount", data.resolved.payment.amount === data.answer.amount, "An explicit field answer selects the amount.");
    } else if (task === "policy") {
      fact("Deciding rule", decision.deciding_rule);
      fact("Evaluation time", decision.evaluated_at);
      fact("Source", "Fixture replay · not live speech or chain execution");
      fact("Mock executor called", data.submission.receipt.mock_executor_called);
      fact("On-chain execution attempted", data.submission.receipt.execution_attempted);
      $("boundary").textContent = "The Allow example calls only a local mock. Denial examples never reach that mock. Records are not transaction receipts.";
      for (const c of decision.checks) check(c.name, c.passed, c.details);
      for (const e of data.audit_timeline || []) $("trace").append(node("li", `${e.step}: ${e.result}`));
    } else {
      if (data.proof) data = data.proof;
      fact("Payment", `${data.input.payment.amount} ${data.input.payment.token}`);
      fact("Recipient", data.input.payment.recipient);
      fact("Inspected public sender · no ownership claim", data.input.sender);
      fact("Network", "Pharos Pacific Mainnet · chain 1672");
      fact("Collected at", data.evaluated_at);
      fact("State block", `${data.state.block_number} · ${data.state.block_hash}`);
      fact("Deciding rule", decision.deciding_rule);
      fact("Skill", `${data.skill.name} / ${data.skill.handler}`);
      $("boundary").textContent = "Historical read-only simulation. Candidate transaction fields are expired examples, not a wallet instruction. Pending nonce and fee quotes were not pinned.";
      for (const c of decision.checks) check(c.name, c.passed, c.details);
      for (const e of data.trace) {
        const li = node("li", `${e.tool}${e.error ? " · error recorded" : ""}`);
        li.append(node("small", JSON.stringify(e.arguments))); $("trace").append(li);
      }
    }
    $("loading").textContent = "Recorded evidence loaded. No live chain request was made by this page.";
  } catch (error) {
    if (revision !== generation) return;
    $("loading").textContent = error.message; $("result-heading").textContent = "UNAVAILABLE";
  }
}
function showTask() {
  const key = location.hash.slice(1);
  const task = Object.hasOwn(tasks, key) ? key : "policy";
  const spec = tasks[task];
  const video = $("walkthrough");
  video.pause();
  video.src = `media/${spec.video}.mp4`;
  $("video-download").href = video.src;
  $("subtitle-download").href = `media/${spec.video}.srt`;
  for (const a of document.querySelectorAll("[data-task]")) {
    if (a.dataset.task === task) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  $("category").textContent = "TASK EVIDENCE"; $("task-title").textContent = spec.title;
  $("kind").textContent = spec.kind; $("task-description").textContent = spec.description;
  $("flow-description").textContent = task === "policy"
    ? "Typed command → policy rules → explicit mock approval → policy recheck → local mock or denial. No Chain MCP call or on-chain execution in these policy fixtures."
    : "Text → clarification → Chain MCP state → payment skill decision → human review → recheck → unsigned draft. The local workspace enforces action revisions and cancellation. This viewer only shows saved evidence.";
  $("scenarios").replaceChildren();
  for (const [label, name] of spec.scenarios) {
    const button = node("button", label); button.type = "button";
    button.onclick = () => selectScenario(task, name, button); $("scenarios").append(button);
  }
  selectScenario(task, spec.scenarios[0][1], $("scenarios").firstElementChild);
}
window.addEventListener("hashchange", showTask);
showTask();
