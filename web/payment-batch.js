"use strict";

function renderBatchReview(result) {
  followItem = null;
  $("review-body").append(element("h3", result.payments.length ? `${result.payments.length} payment batch` : "Batch review"));
  if (result.status === "needs_details") {
    const question = result.questions[0];
    const message = `Payment ${question.item + 1}: ${question.question}`;
    $("review-body").append(element("div", message, "notice"));
    $("agent-response-text").textContent = message;
    if (question.choices?.length) {
      const choices = element("ul", "", "address");
      for (const value of question.choices) choices.append(element("li", value));
      $("review-body").append(choices);
    }
    if (!question.requires_rewrite) {
      followItem = question.item;
      followField = question.field;
      $("follow-up").hidden = false;
      $("follow-label").textContent = message;
      $("follow-reason").textContent = "Other rows remain unchanged.";
      $("follow-input").value = "";
      $("follow-input").required = true;
    }
    renderDeveloper(result);
    return;
  }
  if (result.message) $("review-body").append(element("div", result.message, `notice ${result.status === "ready" ? "" : "error"}`));
  const list = element("ol", "", "batch-payments");
  for (const [index, payment] of result.payments.entries()) {
    const item = element("li");
    item.append(element("strong", `${payment.amount} ${payment.token}`),
      element("p", payment.recipient, "address"));
    if (payment.memo) item.append(element("p", `Memo: ${payment.memo}`, "batch-memo"));
    const reviewed = result.items[index];
    item.append(element("small", reviewed
      ? `${labels[reviewed.status] || reviewed.status} · Fee ${reviewed.fee.amount || "unavailable"} PROS`
      : "Not checked"));
    list.append(item);
  }
  $("review-body").append(list);
  const rows = element("div", "", "rows");
  rows.append(row("From", result.wallet.address || "Not configured", "address"),
    row("Network", "Pacific Mainnet · 1672"));
  if (result.aggregate) {
    for (const [token, units] of Object.entries(result.aggregate.totals_base_units)) {
      const decimals = token === "USDC" ? 6 : 18;
      const padded = units.padStart(decimals + 1, "0");
      const amount = `${padded.slice(0, -decimals)}.${padded.slice(-decimals)}`.replace(/\.?0+$/, "");
      rows.append(row(`${token} total`, amount));
    }
    rows.append(row("Total gas budget", `${result.fee.amount} PROS`),
      row("Snapshot", `Block ${result.network.block_number}`),
      row("Ordered nonces", result.nonce_sequence.join(", ")));
  }
  $("review-body").append(rows);
  const checks = element("div", "", "checks");
  for (const check of result.checks) checks.append(checkRow(check));
  $("review-body").append(checks);
  if (result.status === "ready") {
    $("review-body").append(element("p",
      "The full batch reserves one shared issuance allowance, including gas. Cancelled or failed exports keep their reservation. Nonces are not reused locally.", "notice"));
    const confirm = element("label", "", "confirm");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.id = "confirm-details";
    box.addEventListener("change", () => $("draft-button").disabled = !box.checked || busy || Boolean(draft));
    confirm.append(box, document.createTextNode("I checked every amount, full recipient address, memo and nonce order. These are separate unsigned requests."));
    const button = element("button", "Create unsigned batch", "primary");
    button.id = "draft-button";
    button.type = "button";
    button.disabled = true;
    button.addEventListener("click", createDraft);
    $("review-body").append(confirm, button);
  }
  renderDeveloper(result);
}
