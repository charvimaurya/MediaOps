"""
Verification: after an action, wait a settle period, read current
telemetry via the EXISTING detector.prometheus.PrometheusClient, and
apply the EXISTING detector.rules.is_healthy() check. Imports rather than
reimplements those thresholds.
"""

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from detector.prometheus import PrometheusClient
from detector import rules

logger = logging.getLogger(__name__)

# Must exceed prometheus/prometheus.yml's scrape_interval (5s) with
# margin -- an action's effect is only visible to a PromQL query after
# Prometheus has actually re-scraped the target at least once, no matter
# how quickly the underlying exported gauge itself updates. Tests pass
# settle_seconds=0 explicitly to skip this, so bumping the default here
# doesn't affect them.
DEFAULT_SETTLE_SECONDS = 6.0


@dataclass
class VerificationResult:
    passed: bool
    telemetry: dict
    failed_checks: List[str]


class Verifier:
    def __init__(self, prom_client: PrometheusClient, settle_seconds: float = DEFAULT_SETTLE_SECONDS):
        self._prom_client = prom_client
        self._settle_seconds = settle_seconds

    def verify(self, settle_seconds: Optional[float] = None) -> VerificationResult:
        wait = self._settle_seconds if settle_seconds is None else settle_seconds
        if wait > 0:
            time.sleep(wait)

        try:
            telemetry = self._prom_client.get_telemetry()
        except Exception as exc:
            logger.warning("Verification telemetry read failed: %s", exc)
            return VerificationResult(passed=False, telemetry={}, failed_checks=["prometheus_unreachable"])

        passed = rules.is_healthy(telemetry)
        failed_checks = [] if passed else self._explain_failures(telemetry)

        return VerificationResult(passed=passed, telemetry=telemetry, failed_checks=failed_checks)

    @staticmethod
    def _explain_failures(telemetry: dict) -> List[str]:
        """Names which of is_healthy()'s specific conditions didn't hold,
        using detector.rules.THRESHOLDS rather than redefining them."""
        failed: List[str] = []

        fps = telemetry.get("fps")
        if fps is None:
            failed.append("fps_missing")
        elif not (fps >= rules.THRESHOLDS["fps_floor"]):
            failed.append("fps_below_floor")

        cpu_usage = telemetry.get("cpu_usage")
        if cpu_usage is None:
            failed.append("cpu_usage_missing")
        elif not (cpu_usage < rules.THRESHOLDS["cpu_usage_pct"]):
            failed.append("cpu_usage_too_high")

        encoding_latency = telemetry.get("encoding_latency")
        if encoding_latency is None:
            failed.append("encoding_latency_missing")
        elif not (encoding_latency < rules.THRESHOLDS["encoding_latency_ms"]):
            failed.append("encoding_latency_too_high")

        dropped_frames = telemetry.get("dropped_frames")
        if dropped_frames is None:
            failed.append("dropped_frames_missing")
        elif not (dropped_frames < rules.THRESHOLDS["dropped_frames_pct"]):
            failed.append("dropped_frames_too_high")

        encoder_status = telemetry.get("encoder_status")
        if encoder_status is None:
            failed.append("encoder_status_missing")
        elif not (encoder_status == 1):
            failed.append("encoder_status_not_healthy")

        return failed
