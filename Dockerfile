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

COPY app.py /app/app.py

WORKDIR /library

EXPOSE 8098

ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["gunicorn", "--chdir", "/app", "--bind", "0.0.0.0:8098", "--workers", "1", "--threads", "4", "--timeout", "0", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
