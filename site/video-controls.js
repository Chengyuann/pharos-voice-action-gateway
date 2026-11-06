"use strict";

// Keep the public player's labels English regardless of browser UI language.
// Native controls remain in HTML as the no-JavaScript fallback.
(() => {
  const video = document.getElementById("walkthrough");
  const controls = document.getElementById("english-player");
  const play = document.getElementById("video-play");
  const mute = document.getElementById("video-mute");
  const seek = document.getElementById("video-seek");
  const time = document.getElementById("video-time");
  const fullscreen = document.getElementById("video-fullscreen");
  const format = seconds => {
    if (!Number.isFinite(seconds)) return "0:00";
    return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  };
  controls.hidden = false;
  video.controls = false;
  video.tabIndex = 0;
  video.setAttribute("aria-describedby", "video-time");
  const sync = () => {
    play.textContent = video.paused ? "Play" : "Pause";
    play.setAttribute("aria-label", `${video.paused ? "Play" : "Pause"} walkthrough`);
    mute.textContent = video.muted ? "Unmute" : "Mute";
    mute.setAttribute("aria-pressed", String(video.muted));
    time.textContent = `${format(video.currentTime)} / ${format(video.duration)}`;
    seek.value = Number.isFinite(video.duration) && video.duration > 0 ? String(video.currentTime / video.duration * 100) : "0";
    seek.setAttribute("aria-valuetext", time.textContent);
  };
  const toggle = () => {
    if (video.paused) video.play().catch(() => {
      document.getElementById("video-status").hidden = false;
      document.getElementById("video-status").textContent = "Playback did not start. Try the MP4 download.";
    });
    else video.pause();
  };
  play.addEventListener("click", toggle);
  video.addEventListener("click", toggle);
  video.addEventListener("keydown", event => {
    if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggle(); }
  });
  mute.addEventListener("click", () => { video.muted = !video.muted; });
  seek.addEventListener("input", () => {
    if (Number.isFinite(video.duration)) video.currentTime = Number(seek.value) * video.duration / 100;
  });
  fullscreen.addEventListener("click", async () => {
    const panel = video.closest(".video-panel");
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await panel.requestFullscreen();
    } catch { fullscreen.textContent = "Unavailable"; }
  });
  if (!video.closest(".video-panel").requestFullscreen) fullscreen.hidden = true;
  for (const event of ["play", "pause", "ended", "timeupdate", "loadedmetadata", "emptied", "volumechange"]) video.addEventListener(event, sync);
  document.addEventListener("fullscreenchange", () => fullscreen.textContent = document.fullscreenElement ? "Exit full screen" : "Full screen");
  sync();
})();
