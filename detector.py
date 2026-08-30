"""
The Detector -- deterministic front door of the MediaOps CoPilot workflow.

It polls Prometheus for `media_pipeline_health` (1 = healthy, 0 = broken) every
few seconds. When health stays 0 for a sustained window it emits exactly ONE
`AnomalyEvent` (from models.py). While the same problem is ongoing it stays
quiet; once health returns to 1 it resets so a future problem can be detected.

No AI here. Just: poll -> persistence -> dedup.

For now the emitted event is only printed. Step 3 will replace the callback.

Run it:

    python3 detector.py
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Optional

from models import AnomalyEvent, FaultClass, Severity

logger = logging.getLogger("detector")

# --------------------------------------------------------------------------- #
# Config -- module constants, matching the repo's style
# --------------------------------------------------------------------------- #

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
HEALTH_METRIC = "media_pipeline_health"

POLL_INTERVAL_SECONDS = 3.0

# The stream is only declared "genuinely broken" once health has been 0 for at
# least this long. Metric lag from the simulator is ~10s (1 telemetry tick + 1
# scrape), so this must be safely above 10s. This is the knob to turn.
PERSISTENCE_WINDOW_SECONDS = 15.0

QUERY_TIMEOUT_SECONDS = 2.0

# Raw gauges, queried once at emit time to give the AnomalyEvent some context.
# Names come from simulator/telemetry.py.
RAW_METRICS = [
    "media_fps",
    "media_cpu_usage_percent",
    "media_memory_usage_percent",
    "media_bitrate_mbps",
    "media_encoding_latency_ms",
    "media_dropped_frames_percent",
    "media_packet_loss_percent",
    "media_network_latency_ms",
    "media_encoder_status",
]


# --------------------------------------------------------------------------- #
# Prometheus access -- tiny self-contained urllib helpers (no third-party deps)
# --------------------------------------------------------------------------- #

def _query_instant(base_url: str, promql: str) -> Optional[float]:
    """
    Run one Prometheus instant query. Return the first series' value as a float,
    or None on ANY problem (connection refused, timeout, bad JSON, query error,
    metric absent). Never raises -- the detector loop must not die on a blip.
    """
    url = f"{base_url.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": promql})
    try:
        with urllib.request.urlopen(url, timeout=QUERY_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Prometheus unreachable for %r: %s", promql, exc)
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Prometheus returned unparseable body for %r: %s", promql, exc)
        return None

    if payload.get("status") != "success":
        logger.warning("Prometheus query %r not successful: %s", promql, payload.get("error"))
        return None

    results = payload.get("data", {}).get("result", [])
    if not results:
        return None  # metric absent -- caller decides what that means

    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Prometheus value extraction failed for %r: %s", promql, exc)
        return None


def query_health(base_url: str) -> Optional[int]:
    """media_pipeline_health as 0 or 1, or None if it could not be read."""
    value = _query_instant(base_url, HEALTH_METRIC)
    if value is None:
        return None
    return int(value)


def query_raw_snapshot(base_url: str) -> dict[str, float]:
    """
    One instant query per raw gauge. Metrics that error or are missing are
    omitted (never zero-filled) -- an absent metric is not a real 0.
    """
    snapshot: dict[str, float] = {}
    for metric in RAW_METRICS:
        value = _query_instant(base_url, metric)
        if value is not None:
            snapshot[metric] = value
    return snapshot


# --------------------------------------------------------------------------- #
# Default sink for emitted events
# --------------------------------------------------------------------------- #

def print_anomaly(event: AnomalyEvent) -> None:
    """Default `on_anomaly` callback: just print the event. Step 3 swaps this."""
    print("\n" + "=" * 70)
    print("ANOMALY EVENT EMITTED")
    print("=" * 70)
    print(event.model_dump_json(indent=2))
    print("=" * 70 + "\n")


# --------------------------------------------------------------------------- #
# The Detector
# --------------------------------------------------------------------------- #

class Detector:
    """
    Polls health, applies a persistence window, deduplicates, emits one
    AnomalyEvent per problem.
    """

    def __init__(
        self,
        on_anomaly: Optional[Callable[[AnomalyEvent], None]] = None,
        *,
        prometheus_url: str = PROMETHEUS_URL,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
        persistence_window_seconds: float = PERSISTENCE_WINDOW_SECONDS,
    ) -> None:
        self._on_anomaly = on_anomaly or print_anomaly
        self._base_url = prometheus_url
        self._poll_interval = poll_interval_seconds
        self._window = persistence_window_seconds

        # persistence + dedup state
        self._breach_started_at: Optional[float] = None  # time.monotonic() of the first 0
        self._breach_count: int = 0                       # consecutive 0 polls
        self._emitted: bool = False                       # dedup latch for the current problem

    # -- one poll ---------------------------------------------------------- #

    def poll_once(self) -> Optional[AnomalyEvent]:
        """Query health once and feed it to the state machine. Never raises."""
        try:
            health = query_health(self._base_url)
        except Exception:  # defensive -- query_health already swallows, but be safe
            logger.exception("unexpected error querying health")
            health = None
        return self._handle_health(health)

    def _handle_health(self, health: Optional[int]) -> Optional[AnomalyEvent]:
        now = time.monotonic()
        stamp = datetime.now().strftime("%H:%M:%S")

        if health is None:
            # Unknown reading: do not advance the window, do not reset.
            logger.info("[%s] health=?  (no reading from Prometheus)", stamp)
            return None

        if health == 1:
            if self._breach_started_at is not None or self._emitted:
                logger.info("[%s] health=1  (ok) -- resetting", stamp)
            else:
                logger.info("[%s] health=1  (ok)", stamp)
            self._reset()
            return None

        # health == 0 -> breach
        if self._breach_started_at is None:
            self._breach_started_at = now
            self._breach_count = 1
        else:
            self._breach_count += 1
        elapsed = now - self._breach_started_at

        if not self._emitted and elapsed >= self._window:
            logger.info(
                "[%s] health=0  breach %d  elapsed %ds/%ds -- EMITTING",
                stamp, self._breach_count, elapsed, self._window,
            )
            event = self._build_event(elapsed)
            self._emitted = True
            self._on_anomaly(event)
            return event

        state = "already emitted" if self._emitted else f"elapsed {int(elapsed)}s/{int(self._window)}s"
        logger.info("[%s] health=0  breach %d  %s", stamp, self._breach_count, state)
        return None

    # -- helpers ---------------------------------------------------------- #

    def _reset(self) -> None:
        self._breach_started_at = None
        self._breach_count = 0
        self._emitted = False

    def _build_event(self, elapsed: float) -> AnomalyEvent:
        return AnomalyEvent(
            fault_class=FaultClass.UNKNOWN,
            severity=Severity.HIGH,
            reason=(
                f"{HEALTH_METRIC}=0 sustained {int(elapsed)}s "
                f"({self._breach_count} consecutive polls, window {int(self._window)}s)"
            ),
            health_value=0,
            telemetry_snapshot=query_raw_snapshot(self._base_url),
            breach_count=self._breach_count,
        )

    # -- run loop ------------------------------------------------------- #

    def run_forever(self) -> None:
        logger.info(
            "Detector polling %s for %s every %.0fs (persistence window %.0fs)",
            self._base_url, HEALTH_METRIC, self._poll_interval, self._window,
        )
        try:
            while True:
                self.poll_once()
                time.sleep(self._poll_interval)
        except KeyboardInterrupt:
            logger.info("stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    Detector().run_forever()
