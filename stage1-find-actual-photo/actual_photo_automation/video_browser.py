from __future__ import annotations

import html
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .auth import ZohoAuth
from .workdrive import WorkDrive

logger = logging.getLogger(__name__)


APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <div class="shell">
    <header class="masthead">
      <div>
        <p class="kicker">Internal Video Browser</p>
        <h1>{title}</h1>
        <p class="subcopy">Videos load per item, not as one blocking batch. A video can start as soon as its own stream is ready.</p>
      </div>
      <div class="actions">
        <button id="refreshButton" class="action">Refresh Library</button>
      </div>
    </header>

    <section class="controlbar">
      <label class="search">
        <span>Filter</span>
        <input id="searchInput" type="search" placeholder="Search file name or folder path">
      </label>
      <div class="statusline">
        <span id="libraryCount">Loading library...</span>
        <span id="libraryState" class="pill">Connecting</span>
      </div>
    </section>

    <main>
      <section id="gallery" class="gallery" aria-live="polite"></section>
    </main>
  </div>
  <script src="/app.js" defer></script>
</body>
</html>
"""


APP_CSS = """
:root {
  --bg: #0b1115;
  --panel: #10181d;
  --panel-strong: #142128;
  --line: rgba(177, 214, 204, 0.16);
  --text: #ecf3ef;
  --muted: #97aba5;
  --accent: #89d1bb;
  --accent-strong: #bfead9;
  --warning: #efc17a;
  --shadow: 0 24px 60px rgba(0, 0, 0, 0.28);
}

* {
  box-sizing: border-box;
}

html, body {
  margin: 0;
  min-height: 100%;
  background:
    radial-gradient(circle at top right, rgba(137, 209, 187, 0.12), transparent 28%),
    linear-gradient(180deg, #0b1115 0%, #0d151a 46%, #0a1014 100%);
  color: var(--text);
  font-family: "Segoe UI Variable", "Segoe UI", "Helvetica Neue", sans-serif;
}

body {
  min-height: 100svh;
}

button, input {
  font: inherit;
}

.shell {
  width: min(1480px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 24px 0 48px;
}

.masthead {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  align-items: end;
  padding: 28px 0 20px;
}

.kicker {
  margin: 0 0 8px;
  color: var(--accent);
  font-size: 12px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
}

.masthead h1 {
  margin: 0;
  font-size: clamp(34px, 5vw, 64px);
  line-height: 0.94;
  letter-spacing: -0.04em;
}

.subcopy {
  max-width: 760px;
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 15px;
  line-height: 1.6;
}

.actions {
  display: flex;
  justify-content: flex-end;
}

.action {
  padding: 12px 18px;
  border: 1px solid var(--line);
  background: rgba(16, 24, 29, 0.9);
  color: var(--text);
  border-radius: 999px;
  cursor: pointer;
  transition: transform 140ms ease, border-color 140ms ease, background 140ms ease;
}

.action:hover {
  transform: translateY(-1px);
  border-color: rgba(137, 209, 187, 0.45);
  background: rgba(20, 33, 40, 1);
}

.controlbar {
  position: sticky;
  top: 0;
  z-index: 5;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: end;
  padding: 18px 0 18px;
  backdrop-filter: blur(18px);
  background: linear-gradient(180deg, rgba(11, 17, 21, 0.92), rgba(11, 17, 21, 0.78));
  border-top: 1px solid rgba(177, 214, 204, 0.08);
  border-bottom: 1px solid rgba(177, 214, 204, 0.08);
}

.search {
  display: grid;
  gap: 8px;
}

.search span {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.search input {
  width: min(520px, 100%);
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(16, 24, 29, 0.9);
  color: var(--text);
  outline: none;
}

.search input:focus {
  border-color: rgba(137, 209, 187, 0.54);
  box-shadow: 0 0 0 3px rgba(137, 209, 187, 0.11);
}

.statusline {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--muted);
  font-size: 14px;
}

.pill {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid rgba(137, 209, 187, 0.2);
  background: rgba(137, 209, 187, 0.08);
  color: var(--accent-strong);
}

.gallery {
  display: grid;
  gap: 18px;
  padding-top: 22px;
}

.video-item {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(320px, 0.85fr);
  gap: 18px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 28px;
  background: linear-gradient(180deg, rgba(16, 24, 29, 0.98), rgba(12, 20, 25, 0.96));
  box-shadow: var(--shadow);
  opacity: 0;
  transform: translateY(18px);
  animation: rise 440ms cubic-bezier(.2,.8,.2,1) forwards;
}

.video-item[data-state="loading"] .media-shell {
  border-color: rgba(239, 193, 122, 0.34);
}

.video-item[data-state="ready"] .media-shell {
  border-color: rgba(137, 209, 187, 0.34);
}

.video-copy {
  display: grid;
  align-content: start;
  gap: 14px;
  min-width: 0;
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.meta-row .eyebrow {
  color: var(--accent);
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.meta-row .stamp {
  color: var(--muted);
  font-size: 12px;
}

.video-copy h2 {
  margin: 0;
  font-size: clamp(22px, 3vw, 34px);
  line-height: 1.02;
  letter-spacing: -0.03em;
  overflow-wrap: anywhere;
}

.pathline {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.55;
}

.hint {
  margin: 0;
  color: rgba(236, 243, 239, 0.82);
  font-size: 14px;
  line-height: 1.6;
}

.media-shell {
  position: relative;
  min-height: 260px;
  border-radius: 22px;
  overflow: hidden;
  border: 1px solid rgba(177, 214, 204, 0.14);
  background:
    radial-gradient(circle at top right, rgba(137, 209, 187, 0.12), transparent 24%),
    linear-gradient(180deg, #121d23 0%, #0f171c 100%);
}

.media-shell video {
  width: 100%;
  height: 100%;
  display: block;
  background: #000;
  object-fit: cover;
}

.overlay {
  position: absolute;
  inset: 0;
  display: grid;
  align-content: end;
  gap: 14px;
  padding: 18px;
  background: linear-gradient(180deg, rgba(8, 11, 14, 0.08), rgba(8, 11, 14, 0.72));
  transition: opacity 180ms ease;
}

.video-item[data-active="true"] .overlay {
  opacity: 0;
  pointer-events: none;
}

.overlay-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.state-tag {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 0 12px;
  border-radius: 999px;
  background: rgba(11, 17, 21, 0.7);
  border: 1px solid rgba(177, 214, 204, 0.12);
  color: var(--text);
  font-size: 13px;
}

.state-tag::before {
  content: "";
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--warning);
}

.video-item[data-state="ready"] .state-tag::before {
  background: var(--accent);
}

.overlay button {
  width: max-content;
  padding: 12px 18px;
  border: 0;
  border-radius: 999px;
  background: var(--accent);
  color: #091014;
  cursor: pointer;
  font-weight: 600;
}

.empty-state,
.error-state {
  padding: 28px 0;
  color: var(--muted);
  font-size: 16px;
}

@keyframes rise {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 980px) {
  .shell {
    width: min(100vw - 20px, 1480px);
  }

  .masthead,
  .controlbar,
  .video-item {
    grid-template-columns: 1fr;
  }

  .actions {
    justify-content: flex-start;
  }

  .media-shell {
    min-height: 220px;
  }
}
"""


APP_JS = """
const state = {
  videos: [],
  filter: "",
  observer: null,
  loading: false,
};

const gallery = document.getElementById("gallery");
const libraryCount = document.getElementById("libraryCount");
const libraryState = document.getElementById("libraryState");
const searchInput = document.getElementById("searchInput");
const refreshButton = document.getElementById("refreshButton");

function setLibraryState(label, tone = "default") {
  libraryState.textContent = label;
  libraryState.dataset.tone = tone;
}

function formatDate(value) {
  if (!value) {
    return "Unknown update";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function filteredVideos() {
  const needle = state.filter.trim().toLowerCase();
  if (!needle) {
    return state.videos;
  }
  return state.videos.filter((video) => {
    return [video.name, video.path, video.folder_path]
      .filter(Boolean)
      .some((part) => String(part).toLowerCase().includes(needle));
  });
}

function pauseOtherVideos(activeVideo) {
  document.querySelectorAll(".video-item video").forEach((video) => {
    if (video !== activeVideo) {
      video.pause();
    }
  });
}

function setTileState(tile, nextState, active = false) {
  tile.dataset.state = nextState;
  tile.dataset.active = active ? "true" : "false";
  const tag = tile.querySelector(".state-tag");
  if (!tag) {
    return;
  }
  if (nextState === "ready") {
    tag.textContent = "Ready to play";
    return;
  }
  if (nextState === "loading") {
    tag.textContent = "Loading this video";
    return;
  }
  tag.textContent = "Queued";
}

function attachVideo(tile, autoplay = false) {
  if (tile.dataset.state === "loading" || tile.dataset.state === "ready") {
    if (autoplay) {
      const video = tile.querySelector("video");
      video.preload = "auto";
      pauseOtherVideos(video);
      video.play().catch(() => {});
      setTileState(tile, "ready", true);
    }
    return;
  }

  const video = tile.querySelector("video");
  const streamUrl = tile.dataset.streamUrl;
  if (!streamUrl) {
    return;
  }

  setTileState(tile, "loading", autoplay);
  video.preload = autoplay ? "auto" : "metadata";
  video.src = streamUrl;
  video.load();

  if (autoplay) {
    pauseOtherVideos(video);
    video.play().catch(() => {});
  }
}

function observeTiles() {
  if (state.observer) {
    state.observer.disconnect();
  }
  state.observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) {
        return;
      }
      attachVideo(entry.target, false);
      state.observer.unobserve(entry.target);
    });
  }, {
    rootMargin: "220px 0px",
    threshold: 0.15,
  });

  document.querySelectorAll(".video-item").forEach((tile) => {
    state.observer.observe(tile);
  });
}

function renderGallery() {
  const videos = filteredVideos();
  if (state.observer) {
    state.observer.disconnect();
  }
  libraryCount.textContent = `${videos.length} video${videos.length === 1 ? "" : "s"} visible`;

  if (!videos.length) {
    gallery.innerHTML = '<p class="empty-state">No videos matched the current filter.</p>';
    return;
  }

  gallery.innerHTML = videos.map((video, index) => `
    <article class="video-item" data-id="${escapeHtml(video.id)}" data-stream-url="${escapeHtml(video.stream_url)}" data-state="idle" data-active="false" style="animation-delay:${Math.min(index * 45, 420)}ms">
      <div class="video-copy">
        <div class="meta-row">
          <span class="eyebrow">WorkDrive stream</span>
          <span class="stamp">${escapeHtml(formatDate(video.modified_at))}</span>
        </div>
        <h2>${escapeHtml(video.name)}</h2>
        <p class="pathline">${escapeHtml(video.path || video.folder_path || "")}</p>
        <p class="hint">This tile loads independently. Once this stream is ready, you can play it without waiting for the rest of the library.</p>
      </div>
      <div class="media-shell">
        <video controls playsinline preload="none"></video>
        <div class="overlay">
          <div class="overlay-top">
            <span class="state-tag">Queued</span>
          </div>
          <button type="button" class="play-button">Load and Play</button>
        </div>
      </div>
    </article>
  `).join("");

  document.querySelectorAll(".video-item").forEach((tile) => {
    const video = tile.querySelector("video");
    const playButton = tile.querySelector(".play-button");

    playButton.addEventListener("click", () => {
      attachVideo(tile, true);
    });

    video.addEventListener("loadedmetadata", () => {
      setTileState(tile, "ready", false);
    });

    video.addEventListener("canplay", () => {
      setTileState(tile, "ready", tile.dataset.active === "true");
    });

    video.addEventListener("play", () => {
      pauseOtherVideos(video);
      setTileState(tile, "ready", true);
    });

    video.addEventListener("pause", () => {
      if (!video.ended) {
        setTileState(tile, "ready", false);
      }
    });

    video.addEventListener("waiting", () => {
      setTileState(tile, "loading", tile.dataset.active === "true");
    });

    video.addEventListener("error", () => {
      tile.dataset.state = "idle";
      tile.dataset.active = "false";
      tile.querySelector(".state-tag").textContent = "Stream error";
    });
  });

  observeTiles();
}

async function loadVideos(forceRefresh = false) {
  if (state.loading) {
    return;
  }
  state.loading = true;
  setLibraryState("Loading");
  libraryCount.textContent = "Loading library...";

  try {
    const suffix = forceRefresh ? "?refresh=1" : "";
    const response = await fetch(`/api/videos${suffix}`);
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    state.videos = Array.isArray(payload.videos) ? payload.videos : [];
    renderGallery();
    setLibraryState("Ready");
  } catch (error) {
    gallery.innerHTML = `<p class="error-state">${escapeHtml(error.message || "Unable to load videos.")}</p>`;
    libraryCount.textContent = "Library unavailable";
    setLibraryState("Error");
  } finally {
    state.loading = false;
  }
}

searchInput.addEventListener("input", (event) => {
  state.filter = event.target.value || "";
  renderGallery();
});

refreshButton.addEventListener("click", () => {
  loadVideos(true);
});

loadVideos(false);
"""


class VideoBrowserApp:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.auth = ZohoAuth(config)
        self.workdrive = WorkDrive(self.auth, config)
        self._cache_expiry = 0.0
        self._cached_videos: list[dict[str, Any]] = []

    def list_videos(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        cache_seconds = float(self.config.get("video_browser_cache_seconds", 20))
        if (
            not force_refresh
            and self._cached_videos
            and time.time() < self._cache_expiry
        ):
            return list(self._cached_videos)

        folder_ids = list(self.config.get("video_browser_folder_ids") or [])
        if not folder_ids:
            fallback_id = str(self.config.get("workdrive_parent_folder_id", "")).strip()
            if fallback_id:
                folder_ids = [fallback_id]

        depth = int(self.config.get("video_browser_depth", 4))
        items = self.workdrive.list_video_files(folder_ids, max_depth=depth)
        videos = [
            {
                "id": item.item_id,
                "name": item.name,
                "path": item.path,
                "folder_path": item.path.rpartition("/")[0],
                "modified_at": item.modified_at,
                "stream_url": f"/api/stream/{quote(item.item_id)}",
            }
            for item in items
        ]
        self._cached_videos = videos
        self._cache_expiry = time.time() + max(cache_seconds, 0)
        return list(videos)

    def open_video_stream(self, file_id: str, *, range_header: str = ""):
        return self.workdrive.open_file_stream(file_id, range_header=range_header)

    def serve(self, *, host: str | None = None, port: int | None = None) -> None:
        app = self
        bind_host = host or str(self.config.get("video_browser_host", "127.0.0.1"))
        bind_port = int(port or self.config.get("video_browser_port", 8787))
        title = html.escape(
            str(self.config.get("video_browser_title", "HomeCartel WorkDrive Reels"))
        )

        class Handler(BaseHTTPRequestHandler):
            server_version = "ActualPhotoVideoBrowser/0.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                logger.info("%s - %s", self.client_address[0], fmt % args)

            def do_HEAD(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/stream/"):
                    self._serve_stream(unquote(parsed.path.rsplit("/", 1)[-1]), head_only=True)
                    return
                self.send_error(405, "HEAD is not supported for this path")

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path in {"/", "/index.html"}:
                    self._send_text(200, APP_HTML.format(title=title), "text/html; charset=utf-8")
                    return
                if parsed.path == "/app.css":
                    self._send_text(200, APP_CSS, "text/css; charset=utf-8")
                    return
                if parsed.path == "/app.js":
                    self._send_text(200, APP_JS, "application/javascript; charset=utf-8")
                    return
                if parsed.path == "/api/videos":
                    self._serve_videos(parsed.query)
                    return
                if parsed.path.startswith("/api/stream/"):
                    self._serve_stream(unquote(parsed.path.rsplit("/", 1)[-1]), head_only=False)
                    return
                if parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_error(404, "Not found")

            def _serve_videos(self, query: str) -> None:
                params = parse_qs(query)
                force_refresh = params.get("refresh", ["0"])[0] in {"1", "true", "yes"}
                try:
                    videos = app.list_videos(force_refresh=force_refresh)
                except Exception as exc:
                    logger.exception("Failed to load WorkDrive videos")
                    self._send_json(
                        500,
                        {
                            "error": "video_list_failed",
                            "message": str(exc),
                        },
                    )
                    return
                self._send_json(
                    200,
                    {
                        "videos": videos,
                        "count": len(videos),
                    },
                )

            def _serve_stream(self, file_id: str, *, head_only: bool) -> None:
                if not file_id:
                    self.send_error(400, "Missing file id")
                    return

                range_header = self.headers.get("Range", "")
                try:
                    response = app.open_video_stream(file_id, range_header=range_header)
                except Exception as exc:
                    status_code = getattr(getattr(exc, "response", None), "status_code", 502)
                    message = getattr(getattr(exc, "response", None), "text", "")[:500] or str(exc)
                    logger.warning("Failed to proxy stream for %s: %s", file_id, message)
                    self.send_response(status_code)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(message.encode("utf-8", errors="replace"))
                    return

                try:
                    self.send_response(response.status_code)
                    passthrough_headers = [
                        "Accept-Ranges",
                        "Cache-Control",
                        "Content-Disposition",
                        "Content-Length",
                        "Content-Range",
                        "Content-Type",
                        "ETag",
                        "Last-Modified",
                    ]
                    for header_name in passthrough_headers:
                        header_value = response.headers.get(header_name)
                        if header_value:
                            self.send_header(header_name, header_value)
                    if not response.headers.get("Accept-Ranges"):
                        self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()

                    if head_only:
                        return

                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            self.wfile.write(chunk)
                finally:
                    response.close()

            def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_text(self, status_code: int, body: str, content_type: str) -> None:
                encoded = body.encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer((bind_host, bind_port), Handler)
        logger.info(
            "WorkDrive video browser running at http://%s:%s",
            bind_host,
            bind_port,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Video browser stopped by user")
        finally:
            server.server_close()
