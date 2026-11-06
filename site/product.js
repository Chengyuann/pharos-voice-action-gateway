"use strict";

const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
const pointer = matchMedia("(hover: hover) and (pointer: fine)");
const motionToggle = document.getElementById("motion-toggle");
let motionPaused = false;

function updateMotion() {
  const off = motionPaused || motionPreference.matches;
  document.documentElement.dataset.motion = off ? "off" : "on";
  motionToggle.disabled = motionPreference.matches;
  motionToggle.setAttribute("aria-pressed", String(off));
  motionToggle.textContent = motionPreference.matches ? "Reduced motion" : motionPaused ? "Resume motion" : "Pause motion";
}
motionToggle.addEventListener("click", () => { motionPaused = !motionPaused; updateMotion(); });
motionPreference.addEventListener("change", updateMotion);
updateMotion();

const updateVisibility = () => document.documentElement.dataset.pageHidden = String(document.hidden);
document.addEventListener("visibilitychange", updateVisibility); updateVisibility();

for (const surface of document.querySelectorAll("[data-tilt], .spotlight")) {
  let frame = 0;
  const reset = () => {
    cancelAnimationFrame(frame); frame = 0;
    surface.style.removeProperty("--tilt-y"); surface.style.removeProperty("--tilt-x");
    surface.style.setProperty("--spot-opacity", "0");
  };
  surface.addEventListener("pointermove", event => {
    if (motionPaused || motionPreference.matches || !pointer.matches || event.pointerType !== "mouse") return;
    if (frame) return;
    const x = event.clientX, y = event.clientY;
    frame = requestAnimationFrame(() => {
      frame = 0;
      const rect = surface.getBoundingClientRect();
      const px = (x - rect.left) / rect.width, py = (y - rect.top) / rect.height;
      if (surface.dataset.tilt) {
        const strength = Number(surface.dataset.tilt);
        surface.style.setProperty("--tilt-y", `${(px - .5) * strength}deg`);
        surface.style.setProperty("--tilt-x", `${(.5 - py) * strength}deg`);
      }
      surface.style.setProperty("--spot-x", `${px * 100}%`);
      surface.style.setProperty("--spot-y", `${py * 100}%`);
      surface.style.setProperty("--spot-opacity", "1");
    });
  });
  surface.addEventListener("pointerleave", reset);
  surface.addEventListener("pointercancel", reset);
}

if ("IntersectionObserver" in window && !motionPreference.matches) {
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    entry.target.classList.remove("reveal-pending"); observer.unobserve(entry.target);
  }), {threshold:.1, rootMargin:"0px 0px -6%"});
  document.querySelectorAll("[data-reveal]").forEach((element, index) => {
    if (index) element.classList.add("reveal-pending");
    observer.observe(element);
  });
}

const waveform = document.getElementById("sandbox-waveform");
for (let index = 0; index < 39; index++) {
  const bar = document.createElement("i");
  const height = 9 + Math.round(Math.abs(Math.sin(index * .82) * Math.cos(index * .27)) * 50);
  bar.style.setProperty("--bar-height", `${height}px`);
  bar.style.setProperty("--bar-delay", `${(index % 11) * -95}ms`);
  waveform.append(bar);
}

for (const button of document.querySelectorAll("[data-copy]")) {
  if (!navigator.clipboard?.writeText) continue;
  button.hidden = false;
  button.addEventListener("click", async () => {
    const status = document.getElementById("copy-status");
    try {
      await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).textContent.trim());
      status.textContent = "Command copied.";
    } catch { status.textContent = "Clipboard access was blocked. The command is available above."; }
  });
}

function revealAnchor() {
  if (location.hash === "#proof-library") document.querySelector("#proof-library > details").open = true;
}
addEventListener("hashchange", revealAnchor); revealAnchor();
