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
from urllib.parse import urlparse

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

  <style>
    :root {
      color-scheme: light dark;
      font-family:
        Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }

    body {
      max-width: 960px;
      margin: 0 auto;
      padding: 24px;
      background: Canvas;
      color: CanvasText;
    }

    h1 {
      margin-bottom: 4px;
    }

    .subtitle {
      margin-top: 0;
      opacity: 0.72;
    }

    .panel {
      border: 1px solid color-mix(
        in srgb,
        CanvasText 20%,
        transparent
      );
      border-radius: 12px;
      padding: 18px;
      margin-top: 18px;
    }

    label {
      display: block;
      font-weight: 650;
      margin-bottom: 8px;
    }

    input {
      box-sizing: border-box;
      width: 100%;
      padding: 12px;
      border: 1px solid color-mix(
        in srgb,
        CanvasText 28%,
        transparent
      );
      border-radius: 8px;
      font-size: 16px;
    }

    .buttons {
      display: flex;
      gap: 10px;
      margin-top: 12px;
      flex-wrap: wrap;
    }

    button {
      border: 0;
      border-radius: 8px;
      padding: 11px 18px;
      cursor: pointer;
      font-weight: 650;
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }

    .primary {
      background: #2f7d32;
      color: white;
    }

    .danger {
      background: #a93232;
      color: white;
    }

    .secondary {
      background: color-mix(
        in srgb,
        CanvasText 12%,
        Canvas
      );
      color: CanvasText;
    }

    .status-row {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }

    .badge {
      display: inline-block;
      border-radius: 999px;
      padding: 5px 11px;
      font-weight: 700;
    }

    .idle {
      background: #7774;
    }

    .running {
      background: #b8860b44;
    }

    .success {
      background: #20802044;
    }

    .failed,
    .cancelled {
      background: #a0202044;
    }

    pre {
      box-sizing: border-box;
      height: 430px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      background: #111;
      color: #ddd;
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 0;
      font-family:
        ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 13px;
      line-height: 1.42;
    }

    .error {
      color: #d43c3c;
      min-height: 1.4em;
      margin-top: 10px;
    }
  </style>
</head>

<body>
  <h1>Äänikirjalataaja</h1>
  <p class="subtitle">
    Liitä Storytel- tai Nextory-kirjan URL ja käynnistä lataus.
  </p>

  <section class="panel">
    <form id="download-form">
      <label for="url">Storytel- tai Nextory-osoite</label>

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
          Lataa
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
    <div class="status-row">
      <strong>Tila:</strong>
      <span id="status" class="badge idle">Valmis</span>
      <span id="current-url"></span>
    </div>

    <pre id="log"></pre>
  </section>

  <script>
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

    async function refreshStatus() {
      const response = await fetch("/api/status");
      const data = await response.json();

      setState(data);
      logBox.textContent = data.logs.join("");
      logBox.scrollTop = logBox.scrollHeight;
    }

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
        and path.name.lower() != "cover.jpg"
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
