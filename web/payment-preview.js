"use strict";
const $ = id => document.getElementById(id);
const examples = {
  complete: "Send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
  missing: "Send PHRS to 0x1111111111111111111111111111111111111111",
  balance: "Send 0.40 PHRS to 0x1111111111111111111111111111111111111111"
};
const labels = {
  ready: "Ready to review", blocked: "Needs a change", needs_details: "Details needed",
  invalid: "Check the request", data_unavailable: "Data unavailable", draft_created: "Draft created"
};
const sources = {
  request: "Your request", demo_token_registry: "Local token settings",
  configured_native_token: "Configured native token", demo_wallet_snapshot: "Sample balance",
  policy_gatekeeper: "Payment rules", live_rpc: "Live network",
  chain_mcp: "Official Chain MCP", chain_mcp_skill: "Payment Skill + Chain MCP"
};
let config = null, review = null, draft = null, followField = "", busy = false;
let revision = 0, expiryTimer = null, records = [];
let actionSession = null, actionQueue = Promise.resolve(), actionSyncError = "";
let cancelling = false, correcting = false;
const answers = {};

function element(tag, text = "", className = "") {
  const value = document.createElement(tag);
  value.textContent = text;
  value.className = className;
  return value;
}
function mode() { return document.querySelector('input[name="mode"]:checked').value; }
function shortAddress(value) { return value ? `${value.slice(0, 10)}…${value.slice(-8)}` : "Not configured"; }
// Display actual app states only. Animation never advances or authorizes a payment.
function setFlowState(state) {
  const views = {
    idle: [0, "AWAITING INTENT", "WAITING FOR COMMITTED SPEECH OR TYPED TEXT"],
    input: [0, "INTENT READY", "INTENT CHANGED · NEW REVIEW REQUIRED"],
    checking: [1, "CHECKING", "CHECKING BALANCE AND PAYMENT RULES"],
    needs_details: [0, "DETAILS NEEDED", "ADD THE MISSING DETAILS TO CONTINUE"],
    ready: [1, "REVIEW READY", "CHECK THE PAYMENT · THEN CONFIRM"],
    preparing: [2, "PREPARING DRAFT", "RECHECKING BEFORE CREATING A DRAFT"],
    draft: [2, "DRAFT SAVED", "SAVED LOCALLY · NOTHING SIGNED OR SENT"],
    blocked: [1, "CHANGE REQUIRED", "A CHECK DID NOT PASS · CHANGE THE PAYMENT"],
    invalid: [0, "CHECK REQUEST", "REVIEW THE REQUEST BEFORE CONTINUING"],
    data_unavailable: [1, "DATA UNAVAILABLE", "CHECK CONNECTION · NO PAYMENT SENT"],
    failed: [1, "CHECK FAILED", "RETRY THE REVIEW · NO PAYMENT SENT"],
    unavailable: [0, "SETUP UNAVAILABLE", "SETTINGS COULD NOT LOAD · RELOAD TO RETRY"],
    cancelling: [0, "CANCELLING", "WAITING FOR SERVER ACKNOWLEDGEMENT"],
    cancelled: [0, "CANCELLED", "ACTION CANCELLED · A NEW REVIEW IS REQUIRED"],
  };
  const [current, heading, message] = views[state] || views.idle;
  document.documentElement.dataset.flow = state;
  $("hero-readout").textContent = heading;
  if ($("flow-status").textContent !== message) $("flow-status").textContent = message;
  document.querySelectorAll("[data-flow-step]").forEach((node, index) => {
    node.classList.toggle("active", index === current);
    node.classList.toggle("complete", index < current || state === "draft");
    if (index === current) node.setAttribute("aria-current", "step");
    else node.removeAttribute("aria-current");
  });
  const responses = {
    idle: "Use a committed payment from the voice demo, or type one. I will ask for missing details before checking it.",
    input: "The request changed. Run a new review; no previous confirmation is carried over.",
    checking: "Checking this request against the selected balance source and payment rules.",
    ready: "The checks are ready. Verify the amount, full address and data source yourself before creating a draft.",
    preparing: "Rechecking the payment before saving an unsigned draft. Nothing will be sent.",
    draft: "The draft was saved locally. It has not been signed or sent.",
    blocked: "A payment check did not pass. Change the request; confirmation cannot bypass a rule.",
    invalid: "I could not parse a supported payment. Use one English send, pay or transfer instruction.",
    data_unavailable: "The required data is unavailable. Check the connection and retry.",
    failed: "The review failed. No payment was sent.",
    unavailable: "Local payment settings could not load. Check configuration and reload.",
  };
  if (responses[state]) $("agent-response-text").textContent = responses[state];
}
function resetIntentSource() {
  $("intent-source").textContent = "INPUT SOURCE / TYPED OR EDITED TEXT";
}
function importVoicePayment(text, source) {
  if (busy || !config) return false;
  if (typeof text !== "string" || !/^(send|pay|transfer)\b/i.test(text) || text.length > 500) return false;
  $("request").value = text;
  $("request").dispatchEvent(new Event("input"));
  $("intent-source").textContent = `FIXTURE TEXT / ${source} · REQUIRES A NEW REVIEW`;
  $("agent-response-text").textContent = "I copied only the committed payment text. The fixture's confirmation and hashes do not authorize or bind this new payment.";
  $("request").focus({ preventScroll: true });
  $("request").scrollIntoView({ block: "center", behavior: document.documentElement.dataset.motion === "off" ? "instant" : "smooth" });
  return true;
}
function setToast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(setToast.timer);
  setToast.timer = setTimeout(() => $("toast").hidden = true, 2500);
}
async function jsonRequest(path, options = {}, timeoutMs = 50000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal });
    let body;
    try { body = await response.json(); }
    catch { throw new Error(`The server returned an unreadable response (${response.status}). Try again.`); }
    if (!response.ok) throw new Error(body.error || `The request failed (${response.status}).`);
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The request timed out. No payment was sent. Check Activity before retrying.");
    throw error;
  } finally { clearTimeout(timer); }
}
function post(path, payload) {
  return jsonRequest(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}
function actionPost(path, payload, session) {
  return jsonRequest(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Action-Token": session.token },
    body: JSON.stringify({ action_id: session.action_id, ...payload }),
  });
}
function queueAction(operation) {
  const request = actionQueue.then(operation);
  actionQueue = request.catch(() => {});
  return request;
}
function renderActionState() {
  $("action-version").textContent = actionSession
    ? `Action ${actionSession.action_id.slice(0, 8)} · version ${actionSession.revision} · ${actionSession.status}`
    : "No active action";
  $("cancel-action").disabled = !actionSession || cancelling;
  $("correct-action").disabled = !actionSession || correcting || cancelling || Boolean(actionSyncError);
  $("review-button").disabled = busy || !config || cancelling || correcting || Boolean(actionSyncError);
}
function actionFailure(error) {
  actionSyncError = error.message;
  $("error").textContent = `${error.message} Reload to start a new action; saved records remain in Activity.`;
  $("error").hidden = false;
  renderActionState();
}
function invalidateServerAction() {
  const session = actionSession;
  if (!session || cancelling) return;
  queueAction(async () => {
    if (session !== actionSession || session.status === "cancelled") return;
    Object.assign(session, await actionPost("/api/payment/action/invalidate", {
      revision: session.revision,
    }, session));
    renderActionState();
  }).catch(error => { if (session === actionSession) actionFailure(error); });
}
async function cancelCurrentAction() {
  if (!actionSession || cancelling) return;
  const session = actionSession;
  cancelling = true;
  invalidate("Cancelling this action. Any already saved draft remains an unsigned historical record.", false);
  const localRevision = revision;
  setBusy(true);
  setFlowState("cancelling");
  $("status").textContent = "Cancelling";
  try {
    await queueAction(async () => {
      Object.assign(session, await actionPost("/api/payment/action/cancel", {}, session));
    });
    if (localRevision !== revision) return;
    actionSession = null;
    actionSyncError = "";
    resetAnswers();
    $("correction-text").value = "";
    setFlowState("cancelled");
    $("status").textContent = "Cancelled";
    $("agent-response-text").textContent = "Action cancelled. A new review and confirmation are required to continue.";
    $("review-body").replaceChildren(element("div", "Action cancelled. Existing downloaded or saved drafts are historical; nothing was signed or sent.", "notice"));
    $("action-change").hidden = false;
    $("action-change").textContent = `Cancelled action ${session.action_id.slice(0, 8)}.`;
  } catch (error) {
    actionFailure(error);
    $("status").textContent = "Cancellation unconfirmed";
  } finally {
    cancelling = false;
    if (localRevision === revision) setBusy(false);
    renderActionState();
  }
}
async function correctCurrentAction(event) {
  event.preventDefault();
  if (!actionSession || correcting || cancelling || actionSyncError) return;
  const text = $("correction-text").value.trim();
  if (!text) return;
  if (/^(?:stop|cancel|abort|取消|停止|暂停|先别转|先别执行)[。.!]?$/i.test(text)) {
    await cancelCurrentAction();
    return;
  }
  const session = actionSession;
  const latestAnswers = { ...answers };
  if (followField && $("follow-input").value.trim()) latestAnswers[followField] = $("follow-input").value.trim();
  const latestInput = { text: $("request").value.trim(), answers: latestAnswers, mode: mode() };
  correcting = true;
  invalidate("The instruction is being corrected. Its previous review is no longer usable.", false);
  const localRevision = revision;
  setBusy(true);
  try {
    const result = await queueAction(async () => {
      Object.assign(session, await actionPost("/api/payment/action/update", {
        revision: session.revision, input: latestInput,
      }, session));
      const updated = await actionPost("/api/payment/action/correct", { revision: session.revision, text }, session);
      Object.assign(session, updated);
      return updated;
    });
    if (localRevision !== revision || cancelling) return;
    resetAnswers();
    resetIntentSource();
    showDirectFields(null);
    $("request").value = result.input.text;
    $("correction-text").value = "";
    $("action-change").hidden = false;
    $("action-change").textContent = result.change
      ? `Amount: ${result.change.before} → ${result.change.after}. Previous review invalidated.`
      : result.message;
    $("agent-response-text").textContent = result.message;
    $("status").textContent = "Needs review";
  } catch (error) {
    actionFailure(error);
  } finally {
    correcting = false;
    if (localRevision === revision) setBusy(false);
    renderActionState();
  }
}
function resetAnswers() {
  for (const key of Object.keys(answers)) delete answers[key];
  followField = "";
  $("follow-up").hidden = true;
  $("follow-input").required = false;
  $("review-button").textContent = "Review payment →";
}
function invalidate(message = "Payment details changed. Run the checks again before creating a draft.", notifyServer = true) {
  revision++;
  const hadResult = Boolean(review || draft || busy);
  review = null;
  draft = null;
  clearTimeout(expiryTimer);
  setBusy(false);
  if (notifyServer) invalidateServerAction();
  $("error").hidden = !actionSyncError;
  setFlowState(config ? ($("request").value.trim() ? "input" : "idle") : "unavailable");
  if (hadResult) {
    $("status").textContent = "Needs review";
    $("status").className = "pill stale";
    $("review-time").textContent = "Current details have not been checked";
    $("review-body").replaceChildren(element("div", message, "notice"));
  }
}
function scheduleExpiry(record) {
  clearTimeout(expiryTimer);
  const remaining = Date.parse(record.expires_at) - Date.now();
  if (remaining <= 0) { invalidate("This review has expired. Run the checks again."); return; }
  expiryTimer = setTimeout(() => invalidate("This review has expired. Run the checks again. Historical records remain in Activity."), remaining);
}
function setBusy(value) {
  busy = value;
  $("payment-form").setAttribute("aria-busy", String(value));
  for (const control of $("payment-form").elements) control.disabled = value;
  $("review-button").disabled = value || !config;
  if ($("confirm-details")) $("confirm-details").disabled = value || Boolean(draft);
  if ($("draft-button")) $("draft-button").disabled = value || Boolean(draft) || !$("confirm-details")?.checked;
  document.dispatchEvent(new Event("payment-availability"));
  renderActionState();
}
function setLoading(message) {
  setFlowState("checking");
  $("review-body").replaceChildren();
  const box = element("div", "", "loading");
  box.append(element("div"), element("div"), element("div"), element("p", message));
  $("review-body").append(box);
  $("status").textContent = "Checking";
  $("status").className = "pill muted";
}
function setModeText() {
  const live = mode() === "live", mcp = mode() === "mcp";
  $("hero-source").textContent = mcp ? "OFFICIAL CHAIN MCP / MAINNET / READ ONLY" : live ? "VOICE FIXTURE + READ-ONLY RPC" : "VOICE FIXTURE + DEMO PAYMENT";
  $("environment").textContent = mcp ? "Mainnet · unsigned only" : live ? "Fixture + live" : "Fixture + demo";
  $("mode-note").textContent = mcp ? "Pacific Mainnet (1672): PROS, USDC, WPROS. Real reads and simulation through official Chain MCP; nothing signed or sent."
    : live
    ? (config?.live_available ? "Reads the configured address, balance and estimated fee. Nothing is signed." : "Live mode needs a server connection. Open Connection for setup instructions.")
    : `Sample balance; rules evaluated at ${config?.wallet.policy_time || "the configured test time"}. No network request.`;
  const wallet = mcp ? config?.mcp_wallet : live ? config?.live_wallet : config?.wallet.wallet;
  $("account-title").textContent = live || mcp ? "Read-only address · not a connected wallet" : "Example account";
  $("account-address").textContent = wallet?.address || "No address configured";
  $("account-balance").textContent = live || mcp ? "Balance checked during review" : `${config?.wallet.balances.PHRS || "—"} PHRS · Demo data`;
  if (config) renderSettings();
}
function showDirectFields(payment) {
  $("edit-amount").value = payment?.amount || "";
  $("edit-recipient").value = payment?.recipient || "";
  const token = payment?.token || (mode() === "mcp" ? "PROS" : "PHRS");
  $("edit-token").replaceChildren();
  for (const symbol of mode() === "mcp" ? ["PROS", "USDC", "WPROS"] : ["PHRS"]) {
    const option = element("option", symbol);
    option.value = symbol;
    $("edit-token").append(option);
  }
  if (![...$("edit-token").options].some(option => option.value === token)) {
    const option = element("option", `${token} (not supported)`);
    option.value = token;
    option.dataset.temporary = "true";
    $("edit-token").append(option);
  }
  $("edit-token").value = token;
}
function editDirectFields() {
  if (busy) return;
  resetIntentSource();
  resetAnswers();
  const amount = $("edit-amount").value.trim(), token = $("edit-token").value, recipient = $("edit-recipient").value.trim();
  $("request").value = `Send ${amount} ${token}${recipient ? ` to ${recipient}` : ""}`.replace(/\s+/g, " ");
  invalidate();
}
function row(label, value, className = "") {
  const item = element("div", "", `row ${className}`);
  item.append(element("span", label), element("strong", value));
  return item;
}
function checkRow(check) {
  const item = element("div", "", `check ${check.passed ? "pass" : "fail"}`);
  const detail = element("div");
  detail.append(element("strong", check.label), element("p", `${check.detail} · ${sources[check.source] || check.source}`));
  const indicator = element("span", check.passed ? "✓" : "!");
  indicator.setAttribute("aria-label", check.passed ? "Passed" : "Failed");
  item.append(indicator, detail);
  return item;
}
function renderDeveloper(result) {
  const details = element("details", "", "developer");
  details.append(element("summary", "Developer details"), element("pre", JSON.stringify(result, null, 2)));
  $("review-body").append(details);
}
function renderReview(result) {
  setFlowState(result.status);
  review = result;
  draft = null;
  followField = "";
  $("follow-up").hidden = true;
  $("follow-input").required = false;
  $("error").hidden = true;
  $("status").textContent = labels[result.status] || result.status;
  $("status").className = `pill ${result.status}`;
  $("review-time").textContent = `Checked ${new Date(result.created_at).toLocaleTimeString()} · ${result.mode === "mcp" ? "Official Chain MCP" : result.mode === "demo" ? "Demo data" : "Live network"}`;
  $("review-body").replaceChildren();
  showDirectFields(result.payment);
  if (result.payment?.amount && result.payment?.token && result.payment?.recipient) {
    $("request").value = `Send ${result.payment.amount} ${result.payment.token} to ${result.payment.recipient}`;
    for (const key of Object.keys(answers)) delete answers[key];
  }
  scheduleExpiry(result);
  if (result.status === "needs_details") {
    $("agent-response-text").textContent = result.question;
    const notice = element("div", result.question, "notice");
    if (result.reason) notice.append(element("p", result.reason));
    $("review-body").append(notice);
    if (!result.requires_rewrite) {
      followField = result.field;
      $("follow-up").hidden = false;
      $("follow-label").textContent = result.question;
      $("follow-reason").textContent = "Your other payment details will stay the same.";
      $("follow-input").value = "";
      $("follow-input").required = true;
      $("follow-input").placeholder = result.field === "recipient" ? "0x…full address" : result.field === "amount" ? "0.02" : result.mode === "mcp" ? "PROS / USDC / WPROS" : "PHRS";
    }
    renderDeveloper(result);
    return;
  }
  if (result.status === "invalid" || result.status === "data_unavailable") {
    $("agent-response-text").textContent = result.message || "This payment could not be checked.";
    $("review-body").append(element("div", result.message || "This payment could not be checked.", `notice ${result.status === "invalid" ? "error" : ""}`));
    if (result.balance) $("review-body").append(row("Available", `${result.balance.available} ${result.payment.token}`));
    if (result.status === "data_unavailable") {
      const retry = element("button", "Retry checks", "plain");
      retry.type = "button";
      retry.onclick = () => $("payment-form").requestSubmit();
      const setup = element("button", "Connection settings", "text-button");
      setup.onclick = () => showView("settings");
      $("review-body").append(retry, element("br"), setup);
    }
    renderDeveloper(result);
    return;
  }
  if (result.status === "blocked") $("review-body").append(element("div", "This payment needs a change before a draft can be created.", "notice error"));
  if (result.message) $("review-body").append(element("div", result.message, "notice"));
  $("review-body").append(element("div", "You send", "amount-label"));
  const amount = element("div", result.payment.amount, "amount");
  amount.append(document.createTextNode(" "), element("small", result.payment.token));
  $("review-body").append(amount);
  const rows = element("div", "", "rows");
  rows.append(row("Recipient", result.payment.recipient, "address"), row("From", result.wallet.address, "address"),
    row("Network", `${result.network.name} · ${result.network.chain_id}`),
    row("Available", result.balance ? `${result.balance.available} ${result.payment.token}` : "Not checked"),
    row("Network fee", result.fee.status === "estimated" ? `${result.fee.amount} ${result.fee.symbol || result.payment.token} (gas limit budget)` : "Not checked"));
  if (result.mode === "mcp") {
    rows.append(row("State snapshot", result.network.block_number ? `Block ${result.network.block_number}` : "Not available"),
      row("Fee / nonce", "Current gas estimate · pending nonce (not pinned)"),
      row("Payment + fee", `${result.payment.amount} ${result.payment.token} + ${result.fee.amount || "?"} PROS`, "total"));
  } else if (result.fee.status === "estimated") rows.append(row("Estimated total", `${result.balance.required} ${result.payment.token}`, "total"));
  else rows.append(row("Amount only", `${result.payment.amount} ${result.payment.token} · fee excluded`, "total"));
  $("review-body").append(rows);
  const checks = element("div", "", "checks");
  checks.append(element("h3", "Checks"));
  for (const [index, check] of result.checks.entries()) {
    const item = checkRow(check);
    item.style.setProperty("--reveal-delay", `${Math.min(index, 8) * 45}ms`);
    checks.append(item);
  }
  $("review-body").append(checks);
  if (result.status === "ready") {
    const confirm = element("label", "", "confirm"), box = document.createElement("input");
    box.type = "checkbox";
    box.id = "confirm-details";
    box.addEventListener("change", () => $("draft-button").disabled = !box.checked || busy || Boolean(draft));
    confirm.append(box, document.createTextNode("I checked the amount, recipient and data source above."));
    const button = element("button", result.draft_label, "primary");
    button.id = "draft-button";
    button.type = "button";
    button.disabled = true;
    button.addEventListener("click", createDraft);
    $("review-body").append(confirm, button);
  }
  renderDeveloper(result);
}
async function submitReview(event) {
  event?.preventDefault();
  if (busy || !config || cancelling || correcting || actionSyncError) return;
  if (followField) {
    const value = $("follow-input").value.trim();
    if (!value) { $("follow-input").reportValidity(); return; }
    answers[followField] = value;
  }
  const payload = { text: $("request").value.trim(), answers: { ...answers }, mode: mode() };
  review = null;
  draft = null;
  clearTimeout(expiryTimer);
  const requestRevision = ++revision;
  setBusy(true);
  $("review-button").textContent = "Checking payment…";
  $("error").hidden = true;
  setLoading(mode() === "mcp" ? "Reading official Chain MCP, token metadata and simulation…" : mode() === "live" ? "Reading the network and payment rules…" : "Checking the sample balance and payment rules…");
  try {
    const session = await queueAction(async () => {
      if (requestRevision !== revision) return null;
      if (!actionSession) {
        actionSession = await post("/api/payment/action", {});
        renderActionState();
      }
      if (requestRevision !== revision) return null;
      Object.assign(actionSession, await actionPost("/api/payment/action/update", {
        revision: actionSession.revision, input: payload,
      }, actionSession));
      renderActionState();
      return actionSession;
    });
    if (!session || requestRevision !== revision) return;
    const result = await actionPost("/api/payment/review", { revision: session.revision }, session);
    if (requestRevision === revision) {
      session.status = "reviewed";
      renderActionState();
    }
    if (requestRevision === revision) renderReview(result);
    await loadRecords();
  } catch (error) {
    if (requestRevision !== revision) return;
    setFlowState("failed");
    $("status").textContent = "Check failed";
    $("status").className = "pill invalid";
    $("review-body").replaceChildren(element("div", error.message, "notice error"));
    $("review-time").textContent = "No current review";
    $("error").textContent = error.message;
    $("error").hidden = false;
  } finally {
    if (requestRevision === revision) {
      setBusy(false);
      $("review-button").textContent = followField ? "Continue review →" : "Review payment →";
      if (followField) $("follow-input").focus();
    }
  }
}
async function createDraft() {
  if (!review || busy || draft || cancelling || correcting || actionSyncError || !$("confirm-details")?.checked) return;
  const current = review, requestRevision = revision;
  const session = actionSession;
  if (!session || current.action_id !== session.action_id || current.action_revision !== session.revision) return;
  setBusy(true);
  setFlowState("preparing");
  $("draft-button").textContent = "Rechecking balance and rules…";
  try {
    const payload = { revision: current.action_revision, review_id: current.id };
    await queueAction(async () => {
      if (requestRevision !== revision) return;
      Object.assign(session, await actionPost("/api/payment/action/confirm", payload, session));
    });
    if (requestRevision !== revision || cancelling) return;
    const result = await actionPost("/api/payment/draft", payload, session);
    if (requestRevision !== revision || review !== current) return;
    session.status = "drafted";
    draft = result;
    renderDraft(result);
    await loadRecords();
  } catch (error) {
    if (requestRevision !== revision) return;
    invalidate("The draft was not created. Review the payment again before retrying.");
    $("error").textContent = error.message;
    $("error").hidden = false;
  } finally { if (requestRevision === revision) setBusy(false); }
}
function renderDraft(result) {
  setFlowState("draft");
  const box = element("div", "", "draft");
  box.id = "draft-result";
  box.append(element("h3", result.wallet_ready ? "Unsigned request created" : "Example draft created"), element("p", result.message));
  const details = element("details", "", "developer");
  details.append(element("summary", "View transaction fields"), element("pre", JSON.stringify(result.transaction, null, 2)));
  box.append(details);
  const actions = element("div", "", "draft-actions");
  const copy = element("button", result.wallet_ready ? "Copy unsigned request" : "Copy example report", "plain");
  copy.type = "button";
  copy.onclick = async () => {
    if (draft !== result || Date.now() >= Date.parse(result.expires_at)) { invalidate("This draft expired. Run a new review."); return; }
    try {
      await navigator.clipboard.writeText(JSON.stringify(result.wallet_ready ? result.transaction : result, null, 2));
      setToast("Copied to clipboard");
    } catch { setToast("Clipboard access failed. Use the audit download instead."); }
  };
  const audit = element("a", "Download audit", "plain");
  audit.href = `/api/payment/audit/${result.id}`;
  actions.append(copy, audit);
  if (result.wallet_uri) {
    const uri = element("button", "Copy wallet URI", "plain");
    uri.type = "button";
    uri.onclick = async () => {
      if (draft !== result || Date.now() >= Date.parse(result.expires_at)) {
        invalidate("This draft expired. Run a new review.");
        return;
      }
      try { await navigator.clipboard.writeText(result.wallet_uri); setToast("Unsigned wallet URI copied"); }
      catch { setToast("Clipboard unavailable. Download the audit instead."); }
    };
    actions.append(uri);
  }
  if (result.proof) {
    const proof = element("button", "Download MCP proof", "plain");
    proof.type = "button";
    proof.onclick = () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(result.proof, null, 2)], {type:"application/json"}));
      const link = element("a");
      link.href = url; link.download = `mcp-proof-${result.id}.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    actions.append(proof);
  }
  box.append(actions);
  $("review-body").append(box);
  $("draft-button").textContent = "Draft created";
  $("draft-button").disabled = true;
  $("confirm-details").disabled = true;
  $("status").textContent = "Draft created";
  $("status").className = "pill";
  $("review-time").textContent = `Saved locally · expires ${new Date(result.expires_at).toLocaleTimeString()}`;
  scheduleExpiry(result);
}
function recordCard(record) {
  const item = element("div", "", "record"), payment = element("div");
  payment.append(element("strong", record.amount && record.token ? `${record.amount} ${record.token}` : "Incomplete payment"),
    element("p", `${labels[record.status] || record.status} · ${record.mode === "mcp" ? "Chain MCP" : record.mode === "demo" ? "Demo" : "Live"} · ${shortAddress(record.recipient)}`));
  const time = element("time", record.created_at ? new Date(record.created_at).toLocaleString() : "—");
  const button = element("button", "View", "plain");
  button.type = "button";
  button.setAttribute("aria-label", `View ${record.record_type === "payment_draft" ? "draft" : "review"} ${record.id.slice(0, 8)}`);
  button.onclick = () => openRecord(record.id);
  item.append(payment, time, button);
  return item;
}
function renderRecords(target, list) {
  target.replaceChildren();
  if (!list.length) { target.append(element("p", "No saved reviews yet.", "muted-text")); return; }
  for (const [index, record] of list.entries()) {
    const item = recordCard(record);
    item.style.setProperty("--reveal-delay", `${Math.min(index, 6) * 35}ms`);
    target.append(item);
  }
}
async function loadRecords() {
  try {
    const data = await jsonRequest("/api/payment/recent", {}, 10000);
    records = data.records;
    renderRecords($("recent-list"), records.slice(0, 3));
    renderRecords($("activity-list"), records);
  } catch {
    for (const id of ["recent-list", "activity-list"]) $(id).replaceChildren(element("p", "Records could not be loaded. Try Refresh records.", "muted-text"));
  }
}
async function openRecord(id) {
  try {
    const record = await jsonRequest(`/api/payment/audit/${id}`, {}, 10000);
    $("record-title").textContent = record.record_type === "payment_draft" ? "Saved draft" : "Saved review";
    $("record-json").textContent = JSON.stringify(record, null, 2);
    $("record-download").href = `/api/payment/audit/${id}`;
    $("record-dialog").showModal();
  } catch (error) { setToast(error.message); }
}
function showView(name) {
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== `view-${name}`;
  for (const button of document.querySelectorAll(".nav-item")) {
    const active = button.dataset.view === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  }
  $("breadcrumb").textContent = { payment: "Voice action", activity: "Activity", rules: "Payment rules", settings: "Connection" }[name];
  document.documentElement.dataset.view = name;
  document.dispatchEvent(new Event("workspace-viewchange"));
  if (name === "activity") loadRecords();
  window.scrollTo({ top: 0, behavior: "instant" });
}
function renderSettings() {
  $("demo-settings").replaceChildren(row("Network", `${config.wallet.network.name} · ${config.wallet.network.chain_id}`),
    row("Example address", config.wallet.wallet.address, "address"), row("Balance", `${config.wallet.balances.PHRS} PHRS`), row("Policy time", config.wallet.policy_time));
  const policy = $("policy-content");
  const active = mode() === "mcp" ? config.mcp_policy : config.policy;
  policy.replaceChildren(row("Mode", mode() === "mcp" ? "Official Chain MCP · Pacific Mainnet" : "Atlantic / demo"),
    row("Maximum payment", `${active.max_single_payment} units of each allowed token (not USD value)`),
    row("Allowed token", active.allowed_tokens.join(", ")),
    row("Payment hours", active.allowed_time_windows.map(item => `${item.days.join(", ")} · ${item.start}–${item.end} ${item.timezone}`).join("; ")));
  for (const address of active.trusted_recipients) policy.append(row("Allowed recipient", address, "address"));
  $("live-connection-state").textContent = config.live_available ? `Configured. The server will read ${config.live_wallet.address}. Availability is checked when you review a payment.` : config.live_hint;
  $("mcp-connection-state").textContent = config.mcp_wallet?.address
    ? `Read-only address: ${config.mcp_wallet.address}. Not wallet ownership or authorization.`
    : config.mcp_hint;
}
async function init() {
  try {
    config = await jsonRequest("/api/payment/config", {}, 10000);
    $("review-button").disabled = false;
    document.dispatchEvent(new Event("payment-availability"));
    setModeText();
    setFlowState("idle");
    renderSettings();
    await loadRecords();
  } catch (error) {
    setFlowState("unavailable");
    $("error").textContent = error.message;
    $("error").hidden = false;
    $("review-body").replaceChildren(element("div", "Payment settings could not be loaded. Check the local configuration and reload this page.", "notice error"));
  }
}
$("payment-form").addEventListener("submit", submitReview);
$("correction-form").addEventListener("submit", correctCurrentAction);
$("cancel-action").addEventListener("click", cancelCurrentAction);
$("request").addEventListener("input", () => { resetIntentSource(); resetAnswers(); showDirectFields(null); invalidate(); });
$("follow-input").addEventListener("input", () => { resetIntentSource(); invalidate(); });
for (const id of ["edit-amount", "edit-recipient"]) $(id).addEventListener("input", editDirectFields);
$("edit-token").addEventListener("change", editDirectFields);
for (const input of document.querySelectorAll('input[name="mode"]')) input.addEventListener("change", () => {
  resetAnswers(); invalidate(); setModeText(); showDirectFields(null);
});
for (const button of document.querySelectorAll("[data-example]")) button.onclick = () => {
  if (busy) return;
  $("request").value = mode() === "mcp"
    ? {complete:"Pay alice 0.001 PROS", missing:"Send USDC to alice", balance:"Pay alice 999999 USDC"}[button.dataset.example]
    : examples[button.dataset.example];
  $("request").dispatchEvent(new Event("input"));
};
for (const button of document.querySelectorAll("[data-view]")) button.onclick = () => showView(button.dataset.view);
$("refresh-records").onclick = loadRecords;
$("close-record").onclick = () => $("record-dialog").close();
$("record-dialog").onclick = event => { if (event.target === $("record-dialog")) $("record-dialog").close(); };
init();

// Presentation only: no payment values, checks, or requests are changed here.
function initMotionLayer() {
  const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
  const pointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  const toggle = $("motion-toggle");
  let paused = false;
  const updateMotion = () => {
    const off = paused || preference.matches;
    document.documentElement.dataset.motion = off ? "off" : "on";
    toggle.hidden = false;
    toggle.disabled = preference.matches;
    toggle.setAttribute("aria-pressed", String(off));
    toggle.textContent = preference.matches ? "Reduced motion" : paused ? "Resume motion" : "Pause motion";
  };
  toggle.addEventListener("click", () => { paused = !paused; updateMotion(); });
  preference.addEventListener("change", updateMotion);
  updateMotion();
  document.documentElement.dataset.view = "payment";
  const updateVisibility = () => {
    document.documentElement.dataset.pageHidden = String(document.hidden);
  };
  document.addEventListener("visibilitychange", updateVisibility);
  updateVisibility();

  document.querySelectorAll("[data-tilt], .spotlight-surface").forEach(surface => {
    let frame = 0, point = null;
    const reset = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      surface.style.removeProperty("--tilt-y");
      surface.style.removeProperty("--tilt-x");
      surface.style.setProperty("--spot-opacity", "0");
    };
    surface.addEventListener("pointermove", event => {
      if (paused || preference.matches || !pointer.matches || event.pointerType !== "mouse") return;
      point = { x: event.clientX, y: event.clientY };
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (paused || preference.matches) return;
        const rect = surface.getBoundingClientRect();
        const x = (point.x - rect.left) / rect.width;
        const y = (point.y - rect.top) / rect.height;
        if (surface.dataset.tilt) {
          const strength = Number(surface.dataset.tilt);
          surface.style.setProperty("--tilt-y", `${(x - .5) * strength}deg`);
          surface.style.setProperty("--tilt-x", `${(.5 - y) * strength}deg`);
        }
        surface.style.setProperty("--spot-x", `${x * 100}%`);
        surface.style.setProperty("--spot-y", `${y * 100}%`);
        surface.style.setProperty("--spot-opacity", "1");
      });
    });
    surface.addEventListener("pointerleave", reset);
    surface.addEventListener("pointercancel", reset);
    preference.addEventListener("change", reset);
    toggle.addEventListener("click", reset);
  });

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        entry.target.classList.remove("reveal-pending");
        observer.unobserve(entry.target);
      });
    }, { threshold: .05 });
    document.querySelectorAll("[data-reveal]").forEach(node => {
      node.classList.add("reveal-pending");
      observer.observe(node);
    });
  }
}
initMotionLayer();
