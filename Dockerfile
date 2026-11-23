FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    MOVIE_WATCH_DIR=/app \
    MOVIE_WATCH_CONFIG=/app/config.yaml \
    MOVIE_WATCH_CHROME_PATH=/usr/bin/google-chrome

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      wget \
      xvfb \
      x11vnc \
      fonts-liberation \
      procps \
      libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Google Chrome publishes both amd64 and arm64 Linux builds.
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
      arm64) CHROME_ARCH=arm64 ;; \
      *)     CHROME_ARCH=amd64 ;; \
    esac; \
    wget -q -O /tmp/chrome.deb \
      "https://dl.google.com/linux/direct/google-chrome-stable_current_${CHROME_ARCH}.deb"; \
    apt-get update; \
    apt-get install -y --no-install-recommends /tmp/chrome.deb; \
    rm -f /tmp/chrome.deb; \
    rm -rf /var/lib/apt/lists/*

# Chrome cannot use its sandbox inside Docker. Wrap the binary so the app code
# needs no changes; Playwright launches this wrapper as executable_path.
RUN mv /usr/bin/google-chrome /usr/bin/google-chrome-real \
    && printf '#!/bin/sh\nexec /usr/bin/google-chrome-real --no-sandbox --disable-dev-shm-usage "$@"\n' \
       > /usr/bin/google-chrome \
    && chmod +x /usr/bin/google-chrome

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir -e . \
    && mkdir -p /app/data /app/logs

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Optional VNC for clearing Cloudflare once (see README). Bind to localhost only.
EXPOSE 5900

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["run"]
