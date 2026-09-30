FROM denoland/deno:bin-2.9.4 AS deno
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DENO_DIR="/tmp/deno" \
    XDG_CACHE_HOME="/tmp/cache" \
    PATH="/home/streamnest/.local/bin:${PATH}"

COPY --from=deno /deno /usr/local/bin/deno
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 streamnest

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt
COPY streamnest_api ./streamnest_api

USER streamnest
EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/healthz', timeout=3)"]

CMD ["uvicorn", "streamnest_api.main:app", "--host", "0.0.0.0", "--port", "8788", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
