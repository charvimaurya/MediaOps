"""
Thin Prometheus HTTP API client. Fetches the current value of the media
simulator's telemetry metrics via the instant query endpoint
(/api/v1/query). Never raises on network or parsing failure -- the
detector loop relies on that to stay alive when Prometheus is down.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"
QUERY_TIMEOUT_SECONDS = 2.0

# telemetry key -> exact Prometheus metric name, as registered in
# simulator/telemetry.py. Do not invent names here.
METRIC_QUERIES = {
    "fps": "media_fps",
    "cpu_usage": "media_cpu_usage_percent",
    "memory_usage": "media_memory_usage_percent",
    "bitrate": "media_bitrate_mbps",
    "encoding_latency": "media_encoding_latency_ms",
    "dropped_frames": "media_dropped_frames_percent",
    "packet_loss": "media_packet_loss_percent",
    "network_latency": "media_network_latency_ms",
    "encoder_status": "media_encoder_status",
}


class PrometheusClient:
    def __init__(self, base_url: str = DEFAULT_PROMETHEUS_URL, timeout: float = QUERY_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _query_instant(self, promql: str) -> Optional[float]:
        url = f"{self.base_url}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"

        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Prometheus query failed for %r: %s", promql, exc)
            return None
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Prometheus returned unparseable JSON for %r: %s", promql, exc)
            return None

        if payload.get("status") != "success":
            logger.warning("Prometheus query %r did not succeed: %s", promql, payload)
            return None

        results = payload.get("data", {}).get("result", [])
        if not results:
            logger.warning("Prometheus has no data for %r", promql)
            return None

        try:
            return float(results[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Could not parse Prometheus value for %r: %s", promql, exc)
            return None

    def get_telemetry(self) -> Dict[str, float]:
        """Fetch current values for every telemetry metric. Metrics that
        fail or are missing are simply absent from the returned dict --
        never represented as 0.0, since 0 is meaningful for fps, bitrate,
        and encoder_status."""
        telemetry: Dict[str, float] = {}
        for key, metric_name in METRIC_QUERIES.items():
            value = self._query_instant(metric_name)
            if value is not None:
                telemetry[key] = value
        return telemetry
