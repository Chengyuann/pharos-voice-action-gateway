"use strict";

for (const button of document.querySelectorAll("[data-copy]")) {
  if (!navigator.clipboard?.writeText) continue;
  button.hidden = false;
  button.addEventListener("click", async () => {
    const status = document.getElementById("copy-status");
    try {
      await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).textContent.trim());
      status.textContent = "Command copied.";
    } catch {
      status.textContent = "Clipboard access was blocked. The command is available above.";
    }
  });
}

function revealAnchor() {
  if (location.hash !== "#proof-library") return;
  document.querySelector("#proof-library > details").open = true;
}
window.addEventListener("hashchange", revealAnchor);
revealAnchor();
