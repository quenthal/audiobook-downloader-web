FROM python:3.13-slim

ARG AUDIOBOOK_DL_REF=master

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    XDG_CONFIG_HOME=/config

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        ca-certificates \
        tini \
    && pip install --no-cache-dir \
        "git+https://github.com/jo1gi/audiobook-dl.git@${AUDIOBOOK_DL_REF}" \
        Flask==3.1.1 \
        gunicorn==23.0.0 \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

COPY patches/nextory.py /tmp/nextory.py
COPY patches/networking.py /tmp/networking.py
COPY patches/download.py /tmp/download.py

RUN python3 - <<'PY'
from pathlib import Path
import audiobookdl

package = Path(audiobookdl.__file__).resolve().parent

replacements = {
    Path("/tmp/nextory.py"): package / "sources" / "nextory.py",
    Path("/tmp/networking.py"): package / "sources" / "source" / "networking.py",
    Path("/tmp/download.py"): package / "output" / "download.py",
}

for source, destination in replacements.items():
    destination.write_bytes(source.read_bytes())
    source.unlink()
    print(f"Installed patch: {destination}")
PY

RUN python3 -m py_compile \
    "$(python3 -c 'import audiobookdl, pathlib; print(pathlib.Path(audiobookdl.__file__).resolve().parent / "sources/nextory.py")')" \
    "$(python3 -c 'import audiobookdl, pathlib; print(pathlib.Path(audiobookdl.__file__).resolve().parent / "sources/source/networking.py")')" \
    "$(python3 -c 'import audiobookdl, pathlib; print(pathlib.Path(audiobookdl.__file__).resolve().parent / "output/download.py")')"

COPY app.py /app/app.py

RUN mkdir -p /work /library

WORKDIR /library

EXPOSE 8098

ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["gunicorn", "--chdir", "/app", "--bind", "0.0.0.0:8098", "--workers", "1", "--threads", "4", "--timeout", "0", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
