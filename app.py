from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, render_template_string, request


app = Flask(__name__)

LIBRARY_PATH = os.environ.get("LIBRARY_PATH", "/library")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config")
WORK_PATH = os.environ.get("WORK_PATH", "/work")
OUTPUT_TEMPLATE = os.environ.get(
    "OUTPUT_TEMPLATE",
    "{author}/{title}/{title}",
)

ALLOWED_HOSTS = {
    "storytel.com",
    "www.storytel.com",
    "storytel.fi",
    "www.storytel.fi",
    "nextory.com",
    "www.nextory.com",
}

STORYTEL_SEARCH_URL = (
    "https://api.storytel.net/search/client/web"
)

STORYTEL_STORE = os.environ.get(
    "STORYTEL_STORE",
    "STHP-FI",
)

@dataclass
class DownloadState:
    lock: threading.RLock = field(default_factory=threading.RLock)
    process: subprocess.Popen[str] | None = None
    running: bool = False
    status: str = "idle"
    url: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    return_code: int | None = None
    logs: deque[str] = field(
        default_factory=lambda: deque(maxlen=5000)
    )
    subscribers: list[queue.Queue[str]] = field(default_factory=list)


state = DownloadState()


PAGE = r"""
<!doctype html>
<html lang="fi">
<head>
  <meta charset="utf-8">
  <meta
    name="viewport"
    content="width=device-width, initial-scale=1"
  >
  <title>Äänikirjalataaja</title>

  <link rel="icon" type="image/png" href="{{ url_for('static', filename='icon.png') }}">
  <link rel="apple-touch-icon" href="{{ url_for('static', filename='icon.png') }}">
  <meta name="theme-color" content="#15181c">

  <style>
    :root {
      color-scheme: dark;
      font-family:
        Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;

      --bg: #111418;
      --bg-soft: #171b20;
      --panel: #1a1f25;
      --panel-2: #15191e;
      --line: #2d353d;
      --line-soft: #3a444d;

      --text: #f2ede3;
      --muted: #b9b2a5;

      --accent: #8a9464;
      --accent-strong: #a4ae79;
      --accent-soft: rgba(138, 148, 100, 0.18);

      --warm: #c77361;
      --warm-strong: #db8773;
      --warm-soft: rgba(199, 115, 97, 0.16);

      --danger: #b95656;
      --danger-strong: #d06f6f;

      --shadow:
        0 20px 50px rgba(0, 0, 0, 0.32),
        0 2px 12px rgba(0, 0, 0, 0.18);
    }

    * {
      box-sizing: border-box;
    }

    html, body {
      margin: 0;
      padding: 0;
      min-height: 100%;
      background:
        radial-gradient(
          circle at top,
          rgba(138, 148, 100, 0.10),
          transparent 30%
        ),
        radial-gradient(
          circle at top right,
          rgba(199, 115, 97, 0.08),
          transparent 24%
        ),
        var(--bg);
      color: var(--text);
    }

    body {
      max-width: 780px;
      margin: 0 auto;
      padding: 28px 18px 36px;
    }

    .hero {
      display: flex;
      gap: 18px;
      align-items: center;
      background:
        linear-gradient(
          180deg,
          rgba(255,255,255,0.02),
          rgba(255,255,255,0.00)
        ),
        var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
      box-shadow: var(--shadow);
    }

    .hero-icon {
      width: 78px;
      height: 78px;
      flex: 0 0 78px;
      border-radius: 18px;
      display: block;
      background: #101315;
      box-shadow:
        inset 0 0 0 1px rgba(255,255,255,0.03),
        0 10px 24px rgba(0,0,0,0.22);
    }

    .hero-copy {
      min-width: 0;
    }

    h1 {
      margin: 0 0 6px 0;
      font-size: clamp(1.55rem, 2.3vw, 2rem);
      line-height: 1.1;
      letter-spacing: -0.02em;
    }

    .subtitle {
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }

    .grid {
      display: grid;
      gap: 18px;
      margin-top: 18px;
    }

    .panel {
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: var(--shadow);
    }

    .panel h2 {
      margin: 0 0 14px 0;
      font-size: 1rem;
      font-weight: 750;
      letter-spacing: -0.01em;
    }

    label {
      display: block;
      font-weight: 700;
      margin-bottom: 9px;
      color: var(--text);
    }

    input {
      width: 100%;
      padding: 13px 14px;
      border: 1px solid var(--line-soft);
      border-radius: 12px;
      font-size: 15px;
      background: #101419;
      color: var(--text);
      outline: none;
      transition:
        border-color 0.18s ease,
        box-shadow 0.18s ease,
        background 0.18s ease;
    }

    input::placeholder {
      color: #8f948f;
    }

    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 4px var(--accent-soft);
      background: #0f1317;
    }

    .buttons {
      display: flex;
      gap: 10px;
      margin-top: 14px;
      flex-wrap: wrap;
    }

    button {
      border: 1px solid transparent;
      border-radius: 12px;
      padding: 11px 16px;
      cursor: pointer;
      font-weight: 750;
      font-size: 14px;
      transition:
        transform 0.08s ease,
        opacity 0.15s ease,
        border-color 0.15s ease,
        background 0.15s ease;
    }

    button:hover {
      transform: translateY(-1px);
    }

    button:active {
      transform: translateY(0);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.45;
      transform: none;
    }

    .primary {
      background: linear-gradient(180deg, var(--accent-strong), var(--accent));
      color: #11140f;
      border-color: rgba(255,255,255,0.08);
    }

    .danger {
      background: linear-gradient(180deg, var(--warm-strong), var(--warm));
      color: #fff6f2;
      border-color: rgba(255,255,255,0.06);
    }

    .secondary {
      background: #232931;
      color: var(--text);
      border-color: var(--line-soft);
    }

    .error {
      color: #f0a6a6;
      min-height: 1.4em;
      margin-top: 12px;
      line-height: 1.4;
    }

    .status-row {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }

    .status-label {
      color: var(--muted);
      font-weight: 700;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 32px;
      border-radius: 999px;
      padding: 6px 12px;
      font-weight: 800;
      border: 1px solid transparent;
      letter-spacing: 0.01em;
    }

    .idle {
      background: rgba(148, 158, 168, 0.14);
      border-color: rgba(148, 158, 168, 0.20);
      color: #d5dbe0;
    }

    .running {
      background: var(--warm-soft);
      border-color: rgba(199, 115, 97, 0.30);
      color: #ffd6cb;
    }

    .success {
      background: var(--accent-soft);
      border-color: rgba(138, 148, 100, 0.32);
      color: #d9e3b8;
    }

    .failed,
    .cancelled {
      background: rgba(185, 86, 86, 0.16);
      border-color: rgba(185, 86, 86, 0.32);
      color: #ffcdcd;
    }

    #current-url {
      color: var(--muted);
      word-break: break-word;
      font-size: 0.95rem;
    }

    pre {
      height: 430px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      background: #0d1013;
      color: #ddd9cf;
      border: 1px solid #242b33;
      border-radius: 14px;
      padding: 14px 15px;
      margin: 0;
      font-family:
        ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 13px;
      line-height: 1.48;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.01);
    }

    .footer-note {
      margin-top: 10px;
      color: #8e958f;
      font-size: 0.88rem;
    }
    
    .search-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
    }
    
    .search-results {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }
    
    .search-empty {
      color: var(--muted);
      padding: 12px 0;
    }
    
    .search-card {
      display: grid;
      grid-template-columns: 72px 1fr auto;
      gap: 14px;
      align-items: center;
      padding: 12px;
      background: #101419;
      border: 1px solid var(--line);
      border-radius: 14px;
    }
    
    .search-cover {
      width: 72px;
      height: 72px;
      object-fit: cover;
      border-radius: 10px;
      background: #242a30;
    }
    
    .search-book-title {
      margin: 0 0 5px;
      font-size: 1rem;
      line-height: 1.25;
    }
    
    .search-meta {
      color: var(--muted);
      font-size: 0.88rem;
      line-height: 1.45;
    }
    
    .search-select {
      white-space: nowrap;
    }
    
    .search-loading {
      color: var(--accent-strong);
      padding: 12px 0;
    }

    @media (max-width: 640px) {
      body {
        padding: 16px 12px 24px;
      }

      .hero {
        align-items: flex-start;
      }

      .hero-icon {
        width: 64px;
        height: 64px;
        flex-basis: 64px;
        border-radius: 14px;
      }

      .buttons {
        flex-direction: column;
      }

      .buttons button {
        width: 100%;
      }
          
      .search-row {
        grid-template-columns: 1fr;
      }
    
      .search-card {
        grid-template-columns: 58px 1fr;
      }
    
      .search-cover {
        width: 58px;
        height: 58px;
      }
    
      .search-select {
        grid-column: 1 / -1;
        width: 100%;
      }
      
      pre {
        height: 360px;
      }
    }
  </style>
</head>

<body>
  <header class="hero">
    <img
      class="hero-icon"
      src="{{ url_for('static', filename='icon.png') }}"
      alt="Äänikirjalataajan kuvake"
    >

    <div class="hero-copy">
      <h1>Äänikirjalataaja</h1>
      <p class="subtitle">
        Lataa Storytel- tai Nextory-kirjoja suoraan kirjastoon.
      </p>
    </div>
  </header>

  <main class="grid">
    <section class="panel">
      <h2>Hae Storytelistä</h2>
    
      <form id="search-form">
        <label for="search-query">
          Kirjan nimi, kirjailija tai avainsana
        </label>
    
        <div class="search-row">
          <input
            id="search-query"
            type="search"
            autocomplete="off"
            placeholder="Esimerkiksi Sapkowski"
          >
    
          <button
            id="search-button"
            class="secondary"
            type="submit"
          >
            Hae
          </button>
        </div>
    
        <div id="search-error" class="error"></div>
      </form>
    
      <div id="search-results" class="search-results"></div>
    </section>
    <section class="panel">
      <h2>Uusi lataus</h2>

      <form id="download-form">
        <label for="url">Kirjan URL</label>

        <input
          id="url"
          name="url"
          type="url"
          inputmode="url"
          autocomplete="off"
          placeholder="https://www.storytel.com/fi/books/..."
          required
        >

        <div class="buttons">
          <button
            id="start"
            class="primary"
            type="submit"
          >
            Lataa kirja
          </button>

          <button
            id="cancel"
            class="danger"
            type="button"
            disabled
          >
            Keskeytä
          </button>

          <button
            id="clear"
            class="secondary"
            type="button"
          >
            Tyhjennä loki
          </button>
        </div>

        <div id="error" class="error"></div>
      </form>
    </section>

    <section class="panel">
      <h2>Tilanne</h2>

      <div class="status-row">
        <span class="status-label">Tila</span>
        <span id="status" class="badge idle">Valmis</span>
        <span id="current-url"></span>
      </div>

      <pre id="log"></pre>
      <div class="footer-note">
        Latauslokit näkyvät tässä reaaliajassa.
      </div>
    </section>
  </main>

  <script>
    const searchForm = document.getElementById("search-form");
    const searchQuery = document.getElementById("search-query");
    const searchButton = document.getElementById("search-button");
    const searchError = document.getElementById("search-error");
    const searchResults = document.getElementById("search-results");
    const form = document.getElementById("download-form");
    const urlInput = document.getElementById("url");
    const startButton = document.getElementById("start");
    const cancelButton = document.getElementById("cancel");
    const clearButton = document.getElementById("clear");
    const errorBox = document.getElementById("error");
    const statusBadge = document.getElementById("status");
    const currentUrl = document.getElementById("current-url");
    const logBox = document.getElementById("log");

    const statusLabels = {
      idle: "Valmis",
      running: "Ladataan",
      success: "Valmis",
      failed: "Virhe",
      cancelled: "Keskeytetty"
    };

    function setState(data) {
      const running = data.running === true;

      startButton.disabled = running;
      cancelButton.disabled = !running;
      urlInput.disabled = running;

      statusBadge.textContent =
        statusLabels[data.status] || data.status;

      statusBadge.className =
        "badge " + (data.status || "idle");

      currentUrl.textContent = data.url || "";
    }

    function appendLog(line) {
      logBox.textContent += line;

      if (!line.endsWith("\n")) {
        logBox.textContent += "\n";
      }

      logBox.scrollTop = logBox.scrollHeight;
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }
    
    function renderSearchResults(results) {
      searchResults.textContent = "";
    
      if (results.length === 0) {
        searchResults.innerHTML =
          '<div class="search-empty">Ei äänikirjatuloksia.</div>';
        return;
      }
    
      for (const book of results) {
        const card = document.createElement("article");
        card.className = "search-card";
    
        const authors = (book.authors || []).join(", ");
        const narrators = (book.narrators || []).join(", ");
    
        const hours = book.duration?.hours || 0;
        const minutes = book.duration?.minutes || 0;
    
        const durationText =
          hours > 0
            ? `${hours} h ${minutes} min`
            : `${minutes} min`;
    
        const ratingText =
          book.rating !== null && book.rating !== undefined
            ? ` · ★ ${Number(book.rating).toFixed(1)}`
            : "";
    
        const narratorText =
          narrators
            ? `<div>Lukija: ${escapeHtml(narrators)}</div>`
            : "";
    
        const coverHtml = book.cover
          ? `
            <img
              class="search-cover"
              src="${escapeHtml(book.cover)}"
              alt=""
              loading="lazy"
            >
          `
          : '<div class="search-cover"></div>';
    
        card.innerHTML = `
          ${coverHtml}
    
          <div>
            <h3 class="search-book-title">
              ${escapeHtml(book.title)}
            </h3>
    
            <div class="search-meta">
              <div>${escapeHtml(authors)}</div>
              ${narratorText}
              <div>
                ${escapeHtml(durationText)}
                ${escapeHtml(ratingText)}
              </div>
            </div>
          </div>
    
          <button
            class="primary search-select"
            type="button"
          >
            Valitse
          </button>
        `;
    
        card
          .querySelector(".search-select")
          .addEventListener("click", () => {
            urlInput.value = book.url;
            urlInput.focus();
    
            document
              .getElementById("download-form")
              .scrollIntoView({
                behavior: "smooth",
                block: "center"
              });
          });
    
        searchResults.appendChild(card);
      }
    }
    
    async function refreshStatus() {
      const response = await fetch("/api/status");
      const data = await response.json();

      setState(data);
      logBox.textContent = data.logs.join("");
      logBox.scrollTop = logBox.scrollHeight;
    }

    searchForm.addEventListener("submit", async (event) => {
      event.preventDefault();
    
      const query = searchQuery.value.trim();
    
      searchError.textContent = "";
      searchResults.innerHTML =
        '<div class="search-loading">Haetaan…</div>';
    
      searchButton.disabled = true;
    
      try {
        const response = await fetch(
          `/api/search?q=${encodeURIComponent(query)}`
        );
    
        const data = await response.json();
    
        if (!response.ok) {
          searchResults.textContent = "";
          searchError.textContent =
            data.error || "Haku epäonnistui.";
          return;
        }
    
        renderSearchResults(data.results || []);
      } catch (error) {
        searchResults.textContent = "";
        searchError.textContent =
          "Hakupalveluun ei saatu yhteyttä.";
      } finally {
        searchButton.disabled = false;
      }
    });
    
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      errorBox.textContent = "";

      const response = await fetch("/api/start", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          url: urlInput.value.trim()
        })
      });

      const data = await response.json();

      if (!response.ok) {
        errorBox.textContent =
          data.error || "Latausta ei voitu käynnistää.";
        return;
      }

      logBox.textContent = "";
      setState(data);
    });

    cancelButton.addEventListener("click", async () => {
      errorBox.textContent = "";

      const response = await fetch("/api/cancel", {
        method: "POST"
      });

      const data = await response.json();

      if (!response.ok) {
        errorBox.textContent =
          data.error || "Keskeytys epäonnistui.";
      }

      setState(data);
    });

    clearButton.addEventListener("click", async () => {
      await fetch("/api/clear", {
        method: "POST"
      });

      logBox.textContent = "";
    });

    const events = new EventSource("/api/events");

    events.addEventListener("log", (event) => {
      appendLog(JSON.parse(event.data));
    });

    events.addEventListener("state", (event) => {
      setState(JSON.parse(event.data));
    });

    events.onerror = () => {
      errorBox.textContent =
        "Lokiyhteys katkesi. Selain yrittää yhdistää uudelleen.";
    };

    events.onopen = () => {
      errorBox.textContent = "";
    };

    refreshStatus();
  </script>
</body>
</html>
"""

def normalize_host(hostname: str | None) -> str:
    if not hostname:
        return ""

    return hostname.rstrip(".").lower()


def valid_audiobook_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = normalize_host(parsed.hostname)

    return (
        host in ALLOWED_HOSTS
        or host.endswith(".storytel.com")
        or host.endswith(".storytel.fi")
        or host.endswith(".nextory.com")
    )

def search_storytel(query: str) -> list[dict]:
    parameters = urlencode({
        "query": query,
        "store": STORYTEL_STORE,
        "searchFor": "omni",
        "includeFormats": "abook",
    })

    request_url = f"{STORYTEL_SEARCH_URL}?{parameters}"

    search_request = Request(
        request_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AudiobookDownloader/1.0",
        },
    )

    with urlopen(search_request, timeout=15) as response:
        payload = json.load(response)

    results: list[dict] = []

    for item in payload.get("items", []):
        if item.get("resultType") != "book":
            continue

        audiobook_format = next(
            (
                item_format
                for item_format in item.get("formats", [])
                if item_format.get("type") == "abook"
                and item_format.get("isReleased") is True
            ),
            None,
        )

        if audiobook_format is None:
            continue

        title = item.get("title")
        share_url = item.get("shareUrl")

        if not title or not share_url:
            continue

        cover = audiobook_format.get("cover") or {}
        duration = item.get("duration") or {}

        results.append({
            "title": title,
            "authors": [
                author.get("name", "")
                for author in item.get("authors", [])
                if author.get("name")
            ],
            "narrators": [
                narrator.get("name", "")
                for narrator in item.get("narrators", [])
                if narrator.get("name")
            ],
            "url": share_url,
            "cover": cover.get("url", ""),
            "language": item.get("language", ""),
            "rating": item.get("averageRating"),
            "duration": {
                "hours": int(duration.get("hours") or 0),
                "minutes": int(duration.get("minutes") or 0),
            },
        })

        if len(results) >= 20:
            break

    return results

def broadcast(event_type: str, payload: object) -> None:
    message = (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )

    with state.lock:
        dead: list[queue.Queue[str]] = []

        for subscriber in state.subscribers:
            try:
                subscriber.put_nowait(message)
            except queue.Full:
                dead.append(subscriber)

        for subscriber in dead:
            state.subscribers.remove(subscriber)


def state_payload() -> dict:
    with state.lock:
        return {
            "running": state.running,
            "status": state.status,
            "url": state.url,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "return_code": state.return_code,
        }


def add_log(text: str) -> None:
    with state.lock:
        state.logs.append(text)

    broadcast("log", text)


def clear_work_path() -> None:
    work_path = Path(WORK_PATH)
    work_path.mkdir(parents=True, exist_ok=True)

    for item in work_path.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink(missing_ok=True)


def publish_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary = destination.with_name(
        f".{destination.name}.uploading"
    )

    if temporary.exists() or temporary.is_symlink():
        if temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        else:
            temporary.unlink()

    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def publish_work_path() -> None:
    work_path = Path(WORK_PATH)
    library_path = Path(LIBRARY_PATH)

    library_path.mkdir(parents=True, exist_ok=True)

    files = [
        path
        for path in work_path.rglob("*")
        if path.is_file()
    ]

    if not files:
        raise RuntimeError(
            "Lataus onnistui, mutta työtilasta ei löytynyt "
            "julkaistavia tiedostoja."
        )

    add_log(
        f"\nSiirretään {len(files)} valmista tiedostoa "
        "kirjastoon.\n"
    )

    for source in files:
        relative_path = source.relative_to(work_path)
        destination = library_path / relative_path

        publish_file(source, destination)
        

def run_download(url: str) -> None:
    username = os.environ.get("STORYTEL_USERNAME", "")
    password = os.environ.get("STORYTEL_PASSWORD", "")

    command = [
        "audiobook-dl",
        "--username",
        username,
        "--password",
        password,
        "--database_directory",
        os.path.join(CONFIG_PATH, "db"),
        "--output",
        OUTPUT_TEMPLATE,
        "--skip-downloaded",
        url,
    ]

    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment["TERM"] = "xterm-256color"

    return_code: int | None = None

    try:
        os.makedirs(LIBRARY_PATH, exist_ok=True)
        os.makedirs(CONFIG_PATH, exist_ok=True)
        os.makedirs(WORK_PATH, exist_ok=True)

        clear_work_path()

        add_log(f"$ audiobook-dl {url}\n")
        add_log(f"Työtila: {WORK_PATH}\n")
        add_log(f"Kirjasto: {LIBRARY_PATH}\n\n")

        process = subprocess.Popen(
            command,
            cwd=WORK_PATH,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )

        with state.lock:
            state.process = process

        broadcast("state", state_payload())

        assert process.stdout is not None

        for line in iter(process.stdout.readline, ""):
            add_log(line.replace("\r", "\n"))

        process.stdout.close()
        return_code = process.wait()

        with state.lock:
            state.return_code = return_code

        if return_code == 0:
            add_log(
                "\nLataus ja muodostaminen valmistuivat. "
                "Siirretään valmis kirja kirjastoon.\n"
            )

            publish_work_path()

            add_log(
                "\nValmis kirja siirrettiin kirjastoon.\n"
            )

        with state.lock:
            state.finished_at = time.time()
            state.running = False
            state.process = None

            if state.status == "cancelled":
                pass
            elif return_code == 0:
                state.status = "success"
            else:
                state.status = "failed"

        if return_code == 0:
            add_log("\nLataus valmistui onnistuneesti.\n")
        elif state.status != "cancelled":
            add_log(
                f"\nLataus päättyi virhekoodiin "
                f"{return_code}.\n"
            )

    except Exception as error:
        with state.lock:
            state.running = False
            state.status = "failed"
            state.finished_at = time.time()
            state.process = None

            if return_code is not None:
                state.return_code = return_code

        add_log(f"\nSisäinen virhe: {error}\n")

    finally:
        try:
            clear_work_path()
            add_log("RAM-työtila tyhjennettiin.\n")
        except Exception as cleanup_error:
            add_log(
                "Varoitus: RAM-työtilan tyhjennys "
                f"epäonnistui: {cleanup_error}\n"
            )

        broadcast("state", state_payload())


@app.get("/")
def index() -> str:
    return render_template_string(PAGE)


@app.get("/api/status")
def api_status() -> Response:
    payload = state_payload()

    with state.lock:
        payload["logs"] = list(state.logs)

    return jsonify(payload)

@app.get("/api/search")
def api_search() -> tuple[Response, int] | Response:
    query = request.args.get("q", "").strip()

    if len(query) < 2:
        return jsonify(
            error="Kirjoita vähintään kaksi merkkiä."
        ), 400

    if len(query) > 100:
        return jsonify(
            error="Hakusana on liian pitkä."
        ), 400

    try:
        results = search_storytel(query)
    except Exception:
        app.logger.exception("Storytel search failed")

        return jsonify(
            error="Storytel-haku epäonnistui."
        ), 502

    return jsonify(
        query=query,
        results=results,
    )

@app.post("/api/start")
def api_start() -> tuple[Response, int] | Response:
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()

    if not valid_audiobook_url(url):
        return jsonify(
            error="Anna kelvollinen Storytel- tai Nextory-kirjan URL."
        ), 400

    if not os.environ.get("STORYTEL_USERNAME"):
        return jsonify(
            error="STORYTEL_USERNAME puuttuu kontilta."
        ), 500

    if not os.environ.get("STORYTEL_PASSWORD"):
        return jsonify(
            error="STORYTEL_PASSWORD puuttuu kontilta."
        ), 500

    with state.lock:
        if state.running:
            return jsonify(
                error="Yksi lataus on jo käynnissä."
            ), 409

        state.running = True
        state.status = "running"
        state.url = url
        state.started_at = time.time()
        state.finished_at = None
        state.return_code = None
        state.logs.clear()

    worker = threading.Thread(
        target=run_download,
        args=(url,),
        daemon=True,
    )
    worker.start()

    payload = state_payload()
    broadcast("state", payload)

    return jsonify(payload)


@app.post("/api/cancel")
def api_cancel() -> tuple[Response, int] | Response:
    with state.lock:
        process = state.process

        if not state.running or process is None:
            return jsonify(
                error="Latausta ei ole käynnissä.",
                **state_payload(),
            ), 409

        state.status = "cancelled"

    try:
        os.killpg(process.pid, signal.SIGTERM)

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)

        add_log("\nLataus keskeytettiin.\n")

    except ProcessLookupError:
        pass

    payload = state_payload()
    broadcast("state", payload)

    return jsonify(payload)


@app.post("/api/clear")
def api_clear() -> Response:
    with state.lock:
        if not state.running:
            state.logs.clear()

    return jsonify(ok=True)


@app.get("/api/events")
def api_events() -> Response:
    subscriber: queue.Queue[str] = queue.Queue(maxsize=1000)

    with state.lock:
        state.subscribers.append(subscriber)

    def generate() -> Generator[str, None, None]:
        try:
            yield (
                "event: state\n"
                f"data: {json.dumps(state_payload())}\n\n"
            )

            while True:
                try:
                    message = subscriber.get(timeout=15)
                    yield message
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with state.lock:
                if subscriber in state.subscribers:
                    state.subscribers.remove(subscriber)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
