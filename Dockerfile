# syntax=docker/dockerfile:1

# Copy the official, statically packaged Prometheus executable into the final
# application image instead of downloading an unchecked binary ourselves.
FROM prom/prometheus:v3.5.0 AS prometheus

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    PROMETHEUS_URL=http://127.0.0.1:9090 \
    SIMULATOR_CONTROL_URL=http://127.0.0.1:8001 \
    SIMULATOR_STATE_URL=http://127.0.0.1:8001/state \
    INFRA_METRICS_SOURCE=prometheus_http \
    GRAFANA_EMBED_ENABLED=false

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=prometheus /bin/prometheus /usr/local/bin/prometheus

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY . .

# The generated evidence movie is deliberately not stored in Git. Build it
# deterministically from the tracked source so every fresh image is complete.
RUN python3 simulator/generate_messy_video.py \
    && mkdir -p /var/lib/prometheus simulator/output/primary \
    && useradd --create-home --uid 10001 mediaops \
    && chown -R mediaops:mediaops /app /var/lib/prometheus \
    && chmod 0755 /app/cloud/start.sh

USER mediaops

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/cloud/start.sh"]
