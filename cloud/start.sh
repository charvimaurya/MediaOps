#!/usr/bin/env bash
set -Eeuo pipefail

# Cloud Run sends SIGTERM before replacing an instance. Keep ownership of all
# children here so Prometheus, Uvicorn, telemetry, and FFmpeg stop together.
children=()
shutdown_started=0

shutdown() {
  if [[ "${shutdown_started}" -eq 1 ]]; then
    return
  fi
  shutdown_started=1
  trap - TERM INT EXIT
  if [[ "${#children[@]}" -gt 0 ]]; then
    kill -TERM "${children[@]}" 2>/dev/null || true
    wait "${children[@]}" 2>/dev/null || true
  fi
}

trap shutdown TERM INT EXIT

required_files=(
  "/app/prometheus/prometheus.cloud.yml"
  "/app/simulator/video.mp4"
  "/app/simulator/output/messy_video.mov"
)
for required_file in "${required_files[@]}"; do
  if [[ ! -r "${required_file}" ]]; then
    echo "startup failed: required file is missing or unreadable: ${required_file}" >&2
    exit 1
  fi
done

prometheus_config="/tmp/prometheus.cloud.runtime.yml"
cp /app/prometheus/prometheus.cloud.yml "${prometheus_config}"

# Prometheus does not expand environment variables in its YAML. Build a
# runtime-only remote_write block from non-secret deployment settings while
# reading the access-policy token directly from its Secret Manager mount.
grafana_remote_write_url="${GRAFANA_REMOTE_WRITE_URL:-}"
grafana_metrics_username="${GRAFANA_METRICS_USERNAME:-}"
grafana_token_file="${GRAFANA_CLOUD_TOKEN_FILE:-/var/secrets/grafana/token}"
if [[ -n "${grafana_remote_write_url}" || -n "${grafana_metrics_username}" ]]; then
  if [[ -z "${grafana_remote_write_url}" || -z "${grafana_metrics_username}" ]]; then
    echo "startup failed: both GRAFANA_REMOTE_WRITE_URL and GRAFANA_METRICS_USERNAME are required" >&2
    exit 1
  fi
  grafana_url_pattern='^https://[A-Za-z0-9._:/?&=%+-]+$'
  if [[ ! "${grafana_remote_write_url}" =~ ${grafana_url_pattern} ]]; then
    echo "startup failed: GRAFANA_REMOTE_WRITE_URL must be a valid HTTPS URL" >&2
    exit 1
  fi
  if [[ ! "${grafana_metrics_username}" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "startup failed: GRAFANA_METRICS_USERNAME contains unsupported characters" >&2
    exit 1
  fi
  if [[ ! -r "${grafana_token_file}" ]]; then
    echo "startup failed: Grafana Cloud token file is missing or unreadable: ${grafana_token_file}" >&2
    exit 1
  fi

  cat >>"${prometheus_config}" <<EOF

remote_write:
  - url: "${grafana_remote_write_url}"
    basic_auth:
      username: "${grafana_metrics_username}"
      password_file: "${grafana_token_file}"
    write_relabel_configs:
      - source_labels: [__name__]
        regex: "media_.*"
        action: keep
EOF
  echo "Grafana Cloud remote_write enabled for media_* metrics"
else
  echo "Grafana Cloud remote_write not configured; local Prometheus only"
fi

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-60}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --fail --silent --show-error --max-time 2 "${url}" >/dev/null; then
      echo "${name} ready: ${url}"
      return 0
    fi
    sleep 1
  done
  echo "startup failed: ${name} did not become ready at ${url}" >&2
  return 1
}

echo "starting Prometheus on 127.0.0.1:9090"
prometheus \
  --config.file="${prometheus_config}" \
  --storage.tsdb.path=/var/lib/prometheus \
  --web.listen-address=127.0.0.1:9090 \
  --storage.tsdb.retention.time=2h &
children+=("$!")
wait_for_url "Prometheus" "http://127.0.0.1:9090/-/ready"

echo "starting simulator control API on 127.0.0.1:8001"
python3 -m uvicorn simulator.control_api:app \
  --host 127.0.0.1 \
  --port 8001 &
children+=("$!")
wait_for_url "simulator control API" "http://127.0.0.1:8001/health"
wait_for_url "simulator metrics" "http://127.0.0.1:8000/metrics"

echo "starting ops console on 0.0.0.0:${PORT:-8080}"
python3 -m uvicorn web.ops_console:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" &
children+=("$!")
wait_for_url "ops console" "http://127.0.0.1:${PORT:-8080}/api/health"

echo "MediaOps CoPilot container is ready"

# A dead child means the instance is no longer a coherent simulator. Exit so
# Docker or Cloud Run replaces it instead of serving a partially working UI.
set +e
wait -n "${children[@]}"
status=$?
set -e
echo "critical child process exited with status ${status}; stopping container" >&2
exit "${status}"
