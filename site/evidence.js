"use strict";
const tasks = {
  policy: {
    video: "01-policy-gatekeeper-mcy",
    title: "Policy Gatekeeper", kind: "Recorded policy fixtures",
    description: "See how amount, recipient and time rules allow or block a request. Each decision keeps the rule that determined the result.",
    scenarios: [["Allowed request", "policy-allow"], ["Amount blocked", "policy-deny-amount"],
      ["Recipient blocked", "policy-deny-recipient"], ["Time blocked", "policy-deny-time"]],
  },
  payment: {
    video: "02-payment-agent-mcy",
    title: "Natural-language payment", kind: "Recorded parser and chain results",
    description: "Resolve an ambiguous amount, inspect balances and review a payment draft. Compare an allowed request with an insufficient-balance result.",
    scenarios: [["Clarify amount", "language-demo"], ["PROS / Nov 6", "pros-20261106-live"], ["Batch / Oct 25", "batch-p1-live"], ["PROS / Oct 22", "pros-20261022-live"],
      ["PROS / Oct 21", "pros-allow-live"],
      ["WPROS allowed", "wpros-allow-live"], ["USDC blocked", "usdc-deny-live"]],
  },
  composition: {
    video: "03-skill-chain-mcp-mcy",
    title: "Skill + Chain MCP", kind: "Recorded installed-Skill flow",
    description: "Follow official Chain MCP reads into the installed Pharos Skill and local payment Skill. Compare the same-block balance before allowing a review.",
    scenarios: [["Installed Skill", "installed-skill-flow"], ["Native transfer", "pros-20261022-live"], ["ERC-20 transfer", "wpros-allow-live"],
      ["Blocked transfer", "usdc-deny-live"]],
  },
};
const $ = id => document.getElementById(id);
let generation = 0;
let activeTask = null;
let selectedScenario = null;
function node(tag, text, className = "") {
  const el = document.createElement(tag); el.textContent = text; el.className = className; return el;
}
function fact(name, value) { $("facts").append(node("dt", name), node("dd", String(value))); }
function ruleLabel(value) { return value.replaceAll("_", " "); }
function check(name, passed, detail) {
  const li = node("li", "");
  li.append(node("strong", `${passed ? "Passed" : "Blocked"} / ${ruleLabel(name)}`, passed ? "pass" : "fail"),
    node("small", detail)); $("checks").append(li);
}
function summarize(data, decision, name) {
  if (data.schema === "pharos-batch-evidence/1") {
    $("record-request").textContent = data.review.payments.map(p => `${p.amount} ${p.token}`).join(" + ");
    $("record-outcome").textContent = `${data.review.payments.length} independent unsigned requests; shared balance and fee checks passed.`;
    $("record-time").textContent = `Recorded October 25, 2026 / Block ${data.review.network.block_number}`;
    return;
  }
  const installed = data.schema === "pharos-installed-skill-flow/1";
  const proof = installed ? data.steps[0].proof : data.proof || data;
  const payment = proof.input?.payment || data.intent?.params || data.resolved?.payment;
  $("record-request").textContent = data.request || `${payment.amount} ${payment.token}`;
  if (name === "language-demo") {
    $("record-outcome").textContent = `${data.first.question} Explicit answer: ${data.answer.amount} ${payment.token}.`;
  } else if (installed) {
    $("record-outcome").textContent = data.steps[2].same_block_balance_agrees
      ? "The installed Skill and Chain MCP returned the same balance at the same block."
      : "The balance comparison blocked this review.";
  } else if (data.policy_decision) {
    $("record-outcome").textContent = decision.outcome === "allow"
      ? "All policy rules passed. The recorded executor was a local mock."
      : `Blocked by ${ruleLabel(decision.deciding_rule)}. The mock executor was not called.`;
  } else {
    $("record-outcome").textContent = decision.outcome === "allow"
      ? "The recorded checks allowed a payment review."
      : `Blocked by ${ruleLabel(decision.deciding_rule)}. No candidate draft.`;
  }
  const timestamp = data.created_at || proof.evaluated_at || decision?.evaluated_at;
  const block = installed ? data.steps[0].block : proof.state?.block_number;
  const date = timestamp ? new Date(timestamp).toLocaleDateString("en-US", {
    month: "long", day: "numeric", year: "numeric", timeZone: "UTC",
  }) : "Recorded parser example";
  $("record-time").textContent = `${date}${block === undefined ? "" : ` / Block ${block}`}`;
}
async function selectScenario(task, name, button) {
  const revision = ++generation;
  selectedScenario = { task, name, button };
  for (const b of $("scenarios").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === button));
  $("loading").textContent = "Loading recorded evidence...";
  $("record-summary").setAttribute("aria-busy", "true");
  for (const id of ["facts", "checks", "trace", "raw"]) $(id).replaceChildren();
  $("result-heading").textContent = "Loading"; $("result-heading").className = "";
  for (const id of ["boundary", "record-outcome", "record-time"]) $(id).textContent = "";
  $("record-request").textContent = "Loading the selected record.";
  $("download").hidden = true;
  $("download").removeAttribute("href");
  $("retry-evidence").hidden = true;
  try {
    const response = await fetch(`proofs/${name}.json`, {cache:"no-store"});
    if (!response.ok) throw new Error(`Evidence unavailable (${response.status})`);
    const original = await response.text();
    let data = JSON.parse(original);
    if (revision !== generation) return;
    $("raw").textContent = original;
    const installedFlow = data.schema === "pharos-installed-skill-flow/1";
    const decision = data.schema === "pharos-batch-evidence/1" ? {outcome: data.review.status === "ready" ? "allow" : "deny"} : installedFlow ? {outcome: data.steps[2].decision === "allow_review" ? "allow" : "deny"}
      : data.decision || data.policy_decision || data.proof?.decision;
    summarize(data, decision, name);
    $("result-heading").textContent = decision ? (decision.outcome === "allow" ? "Allowed" : "Blocked") : "Clarify";
    $("result-heading").className = decision?.outcome === "deny" ? "deny" : "";
    if (data.schema === "pharos-batch-evidence/1") {
      const review = data.review;
      fact("Requests", review.payments.length);
      fact("Common block", review.network.block_number);
      fact("Proposed nonces", review.nonce_sequence.join(", "));
      fact("Native fee budget", `${review.aggregate.native_fee_budget_wei} wei`);
      for (const c of review.aggregate.checks) check(c.name, c.passed, "Whole-batch validation");
      for (const p of review.payments) $("trace").append(node("li", `${p.amount} ${p.token} to ${p.recipient}`));
      $("boundary").textContent = "Historical batch evidence. Independent unsigned transactions, not atomic or sequential simulation. Nothing signed or broadcast.";
    } else if (installedFlow) {
      const [mcp, skill, result] = data.steps;
      fact("Run time", data.created_at);
      fact("Runtime", "Local agent following installed skills (skills CLI / Codex)");
      fact("Official Skill", data.installed_skills.official.name);
      fact("Skill version", data.installed_skills.official.reviewed_commit);
      fact("State block", mcp.block);
      fact("Official MCP calls", mcp.calls);
      fact("Read-only Skill command", skill.command.join(" "));
      fact("Cast version", skill.version);
      fact("Inspected public sender", mcp.proof.input.sender);
      check("Official Chain MCP result", mcp.proof.decision.outcome === "allow",
        mcp.proof.decision.deciding_rule);
      check("Installed Skill executed", skill.exit_code === 0,
        `${skill.command[0]} ${skill.command[1]} returned ${skill.stdout} wei`);
      check("Same-block balance agreement", result.same_block_balance_agrees,
        "The candidate is absent if the Skill balance differs from the MCP snapshot.");
      check("No signature or broadcast", data.signed === false && data.broadcast === false,
        "The recorded flow did not sign or broadcast a transaction.");
      $("boundary").textContent = "Recorded local installed-Skill execution, not a hosted Pharos chat. Cast is an additional same-block check; Chain MCP remains the primary source. The public sender is not proof of ownership, and this historical candidate cannot authorize a payment.";
      for (const entry of mcp.proof.trace) {
        const li = node("li", entry.tool);
        li.append(node("small", JSON.stringify(entry.arguments))); $("trace").append(li);
      }
      $("trace").append(node("li", "Installed official Skill: cast balance at the MCP block"),
        node("li", `Payment Skill: ${result.decision} (${result.deciding_rule})`));
    } else if (name === "language-demo") {
      fact("Original instruction", data.request);
      fact("Follow-up", data.first.question);
      fact("Explicit answer", data.answer.amount);
      fact("Resolved payment", JSON.stringify(data.resolved.payment));
      $("boundary").textContent = "Recorded parser output. This clarification did not sign, send, or check a chain balance.";
      check("Ambiguity detected", data.first.status === "needs_details", data.first.reason);
      check("No guessed amount", data.resolved.payment.amount === data.answer.amount, "An explicit field answer selects the amount.");
      $("trace").append(node("li", `Request: ${data.request}`),
        node("li", `Clarification: ${data.first.question}`),
        node("li", `Explicit answer: ${data.answer.amount} ${data.resolved.payment.token}`));
    } else if (task === "policy") {
      fact("Deciding rule", decision.deciding_rule);
      fact("Evaluation time", decision.evaluated_at);
      fact("Source", "Recorded policy fixture");
      fact("Mock executor called", data.submission.receipt.mock_executor_called);
      fact("On-chain execution attempted", data.submission.receipt.execution_attempted);
      $("boundary").textContent = "The Allow example calls only a local mock. Denial examples never reach that mock. Records are not transaction receipts.";
      for (const c of decision.checks) check(c.name, c.passed, c.details);
      for (const e of data.audit_timeline || []) $("trace").append(node("li", `${e.step}: ${e.result}`));
    } else {
      if (data.proof) data = data.proof;
      fact("Payment", `${data.input.payment.amount} ${data.input.payment.token}`);
      fact("Recipient", data.input.payment.recipient);
      fact("Inspected public sender", data.input.sender);
      fact("Network", "Pharos Pacific Mainnet / chain 1672");
      fact("Collected at", data.evaluated_at);
      fact("State block", `${data.state.block_number} / ${data.state.block_hash}`);
      fact("Deciding rule", decision.deciding_rule);
      fact("Skill", `${data.skill.name} / ${data.skill.handler}`);
      $("boundary").textContent = "Historical read-only simulation. Candidate fields are expired examples, not a wallet instruction. Pending nonce and fee quotes were not pinned. Public sender addresses are not proof of ownership.";
      for (const c of decision.checks) check(c.name, c.passed, c.details);
      for (const e of data.trace) {
        const li = node("li", `${e.tool}${e.error ? " / error recorded" : ""}`);
        li.append(node("small", JSON.stringify(e.arguments))); $("trace").append(li);
      }
    }
    $("download").href = `proofs/${name}.json`; $("download").hidden = false;
    $("loading").textContent = "Recorded evidence loaded.";
  } catch {
    if (revision !== generation) return;
    for (const id of ["facts", "checks", "trace", "raw", "boundary", "record-outcome", "record-time"]) $(id).replaceChildren();
    $("loading").textContent = "The record could not be loaded. Retry or open its original JSON.";
    $("result-heading").textContent = "Unavailable";
    $("result-heading").className = "unavailable";
    $("record-request").textContent = "This recorded result is currently unavailable.";
    $("download").href = `proofs/${name}.json`; $("download").hidden = false;
    $("retry-evidence").hidden = false;
  } finally {
    if (revision === generation) $("record-summary").setAttribute("aria-busy", "false");
  }
}
function showTask() {
  const key = location.hash.slice(1);
  const task = Object.hasOwn(tasks, key) ? key : "policy";
  if (task === activeTask) return;
  activeTask = task;
  const spec = tasks[task];
  const video = $("walkthrough");
  video.pause();
  $("video-status").hidden = true;
  $("video-status").textContent = "";
  video.querySelector("track")?.remove();
  const track = document.createElement("track");
  track.kind = "captions";
  track.srclang = "en";
  track.label = "English";
  track.default = false; // English captions are burned into the film; avoid duplicate text.
  track.src = `media/${spec.video}.vtt`;
  track.addEventListener("error", () => {
    if (activeTask !== task) return;
    $("video-status").hidden = false;
    $("video-status").textContent = "English captions are unavailable. The English SRT download is below.";
  });
  video.append(track);
  video.src = `media/${spec.video}.mp4`;
  video.setAttribute("aria-label", `${spec.title} English walkthrough`);
  video.querySelector("p a").href = `media/${spec.video}.mp4`;
  $("walkthrough-title").replaceChildren(document.createTextNode(`${spec.title} `),
    node("span", "/ English walkthrough", "muted"));
  $("video-download").href = `media/${spec.video}.mp4`;
  $("subtitle-download").href = `media/${spec.video}.srt`;
  for (const a of document.querySelectorAll("[data-task]")) {
    if (a.dataset.task === task) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
  }
  $("category").textContent = "Selected demonstration"; $("task-title").textContent = spec.title;
  $("kind").textContent = spec.kind; $("task-description").textContent = spec.description;
  $("flow-description").textContent = task === "policy"
    ? "A typed command passes through policy rules, mock approval and a policy recheck. Allowed fixtures call a local mock; blocked fixtures stop before it."
    : "The local workflow resolves the request, reads Chain MCP state and checks policy before human review and a fresh unsigned-draft check. The sequence below contains only the steps present in the selected record.";
  $("scenarios").replaceChildren();
  for (const [label, name] of spec.scenarios) {
    const button = node("button", label); button.type = "button";
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => selectScenario(task, name, button)); $("scenarios").append(button);
  }
  selectScenario(task, spec.scenarios[0][1], $("scenarios").firstElementChild);
}
// In-page accessibility anchors must not silently reset the selected task.
window.addEventListener("hashchange", () => {
  if (Object.hasOwn(tasks, location.hash.slice(1))) showTask();
});
$("retry-evidence").addEventListener("click", () => {
  if (selectedScenario) selectScenario(selectedScenario.task, selectedScenario.name, selectedScenario.button);
});
$("walkthrough").addEventListener("error", () => {
  $("video-status").hidden = false;
  $("video-status").textContent = "The video could not be played. Try its MP4 download or inspect the record below.";
});
showTask();
