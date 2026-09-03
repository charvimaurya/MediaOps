"""Skeptical dual-domain recovery verification.

Execution success is ignored as evidence of recovery. This module requires a
sustained healthy Prometheus window and an independent healthy-video finding.

    python3 verify_recovery.py <incident_id>
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from detector import PROMETHEUS_URL, query_health, query_raw_snapshot
from incident_recorder import IncidentRecorder
from observability import log_event
from models import (
    Incident,
    IncidentStatus,
    RemediationAction,
    VerificationResult,
    VerificationVerdict,
    VisionFinding,
    VisionSymptom,
)
REQUIRED_METRICS = frozenset({
    "media_pipeline_health",
    "media_encoder_status",
    "media_fps",
    "media_dropped_frames_percent",
    "media_packet_loss_percent",
})


@dataclass(frozen=True)
class VerificationConfig:
    stable_window_seconds: float
    sample_interval_seconds: float
    video_min_confidence: float

    def __post_init__(self) -> None:
        if self.stable_window_seconds <= 0:
            raise ValueError("VERIFY_STABLE_WINDOW_SECONDS must be positive")
        if self.sample_interval_seconds <= 0 or self.sample_interval_seconds > self.stable_window_seconds:
            raise ValueError(
                "VERIFY_SAMPLE_INTERVAL_SECONDS must be positive and no greater than the window"
            )
        if not 0.0 <= self.video_min_confidence <= 1.0:
            raise ValueError("VERIFY_VIDEO_MIN_CONFIDENCE must be between 0 and 1")

    @classmethod
    def from_env(cls) -> "VerificationConfig":
        window = float(os.environ.get("VERIFY_STABLE_WINDOW_SECONDS", "15"))
        interval = float(os.environ.get("VERIFY_SAMPLE_INTERVAL_SECONDS", "3"))
        confidence = float(os.environ.get("VERIFY_VIDEO_MIN_CONFIDENCE", "0.80"))
        return cls(window, interval, confidence)


def read_metrics() -> dict[str, float]:
    """Read the golden signal and its underlying gauges from Prometheus."""
    health = query_health(PROMETHEUS_URL)
    snapshot = query_raw_snapshot(PROMETHEUS_URL)
    if health is not None:
        snapshot["media_pipeline_health"] = float(health)
    return snapshot


def read_healthy_video(incident: Incident, *, fault: str) -> VisionFinding | None:
    """Load the AI observer only when the independent video check is reached."""
    from agents.vision_agent import analyze_frame

    return analyze_frame(incident, fault=fault)


def _action_for(incident: Incident) -> RemediationAction:
    if incident.execution is not None:
        return incident.execution.action
    if incident.safety_decision is not None:
        return incident.safety_decision.action
    if incident.proposal is not None:
        return incident.proposal.action
    # VerificationResult requires an enum action. This grants no authority and
    # is used only in a CANNOT_VERIFY result when the workflow state is absent.
    return RemediationAction.RESTART_ENCODER


def _result(
    incident: Incident,
    *,
    verdict: VerificationVerdict,
    telemetry_ok: bool,
    video_ok: bool,
    health_value: int | None,
    elapsed: float,
    samples: list[dict[str, float]],
    failed_checks: list[str],
    vision: VisionFinding | None = None,
) -> VerificationResult:
    return VerificationResult(
        incident_id=incident.incident_id,
        action=_action_for(incident),
        verdict=verdict,
        recovered=verdict is VerificationVerdict.RECOVERED,
        telemetry_ok=telemetry_ok,
        video_ok=video_ok,
        health_value=health_value,
        stable_window_seconds=max(0.0, elapsed),
        samples=samples,
        failed_checks=failed_checks,
        vision_recheck=vision,
    )


def _unhealthy_reasons(sample: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    if sample["media_pipeline_health"] != 1:
        reasons.append("media_pipeline_health is not 1")
    if sample["media_encoder_status"] != 1:
        reasons.append("media_encoder_status is not 1")
    if sample["media_fps"] < 24:
        reasons.append("media_fps is below 24")
    if sample["media_dropped_frames_percent"] >= 5:
        reasons.append("media_dropped_frames_percent is not below 5")
    if sample["media_packet_loss_percent"] >= 5:
        reasons.append("media_packet_loss_percent is not below 5")
    return reasons


def verify_recovery(
    incident: Incident,
    *,
    config: VerificationConfig | None = None,
    metrics_reader: Callable[[], dict[str, float]] = read_metrics,
    vision_reader: Callable[..., VisionFinding | None] = read_healthy_video,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> VerificationResult:
    """Return RECOVERED only after positive sustained dual-domain evidence."""
    samples: list[dict[str, float]] = []
    start = monotonic()
    elapsed = 0.0
    health_value: int | None = None
    try:
        active_config = config or VerificationConfig.from_env()
        if incident.execution is None:
            return _result(
                incident,
                verdict=VerificationVerdict.CANNOT_VERIFY,
                telemetry_ok=False,
                video_ok=False,
                health_value=None,
                elapsed=0.0,
                samples=samples,
                failed_checks=["execution result is missing"],
            )

        while True:
            try:
                reading = metrics_reader()
            except Exception as exc:
                return _result(
                    incident,
                    verdict=VerificationVerdict.CANNOT_VERIFY,
                    telemetry_ok=False,
                    video_ok=False,
                    health_value=health_value,
                    elapsed=elapsed,
                    samples=samples,
                    failed_checks=[f"metrics unavailable: {type(exc).__name__}: {exc}"],
                )
            missing = sorted(REQUIRED_METRICS - reading.keys())
            if missing:
                return _result(
                    incident,
                    verdict=VerificationVerdict.CANNOT_VERIFY,
                    telemetry_ok=False,
                    video_ok=False,
                    health_value=health_value,
                    elapsed=elapsed,
                    samples=samples,
                    failed_checks=[f"metrics missing: {', '.join(missing)}"],
                )

            sample = {name: float(reading[name]) for name in REQUIRED_METRICS}
            sample["elapsed_seconds"] = elapsed
            sample["sampled_at_epoch"] = datetime.now(timezone.utc).timestamp()
            samples.append(sample)
            health_value = int(sample["media_pipeline_health"])
            unhealthy = _unhealthy_reasons(sample)
            if unhealthy:
                return _result(
                    incident,
                    verdict=VerificationVerdict.RECOVERY_FAILED,
                    telemetry_ok=False,
                    video_ok=False,
                    health_value=health_value,
                    elapsed=elapsed,
                    samples=samples,
                    failed_checks=[f"telemetry relapse/unhealthy: {reason}" for reason in unhealthy],
                )

            elapsed = monotonic() - start
            if elapsed >= active_config.stable_window_seconds:
                break
            sleep(min(active_config.sample_interval_seconds,
                      active_config.stable_window_seconds - elapsed))
            elapsed = monotonic() - start

        try:
            vision = vision_reader(incident, fault="healthy")
        except Exception as exc:
            return _result(
                incident,
                verdict=VerificationVerdict.CANNOT_VERIFY,
                telemetry_ok=True,
                video_ok=False,
                health_value=health_value,
                elapsed=elapsed,
                samples=samples,
                failed_checks=[f"video unavailable: {type(exc).__name__}: {exc}"],
            )
        if vision is None:
            return _result(
                incident,
                verdict=VerificationVerdict.CANNOT_VERIFY,
                telemetry_ok=True,
                video_ok=False,
                health_value=health_value,
                elapsed=elapsed,
                samples=samples,
                failed_checks=["video finding is missing"],
            )
        if vision.confidence < active_config.video_min_confidence:
            return _result(
                incident,
                verdict=VerificationVerdict.CANNOT_VERIFY,
                telemetry_ok=True,
                video_ok=False,
                health_value=health_value,
                elapsed=elapsed,
                samples=samples,
                failed_checks=[
                    f"video confidence {vision.confidence:.3f} is below "
                    f"{active_config.video_min_confidence:.3f}"
                ],
                vision=vision,
            )
        if vision.symptom is not VisionSymptom.NORMAL:
            return _result(
                incident,
                verdict=VerificationVerdict.RECOVERY_FAILED,
                telemetry_ok=True,
                video_ok=False,
                health_value=health_value,
                elapsed=elapsed,
                samples=samples,
                failed_checks=[
                    f"domains disagree: telemetry healthy but video symptom is {vision.symptom.value}"
                ],
                vision=vision,
            )
        return _result(
            incident,
            verdict=VerificationVerdict.RECOVERED,
            telemetry_ok=True,
            video_ok=True,
            health_value=health_value,
            elapsed=elapsed,
            samples=samples,
            failed_checks=[],
            vision=vision,
        )
    except Exception as exc:
        return _result(
            incident,
            verdict=VerificationVerdict.CANNOT_VERIFY,
            telemetry_ok=False,
            video_ok=False,
            health_value=health_value,
            elapsed=elapsed,
            samples=samples,
            failed_checks=[f"verification error: {type(exc).__name__}: {exc}"],
        )


def run_for_incident(
    incident_id: str,
    *,
    recorder: IncidentRecorder | None = None,
    settle_seconds: float | None = None,
    settle_sleep: Callable[[float], None] = time.sleep,
) -> VerificationResult:
    recorder = recorder or IncidentRecorder()
    log_event("verify", incident_id, "verify", "started")
    incident = recorder.load(incident_id)
    incident.status = IncidentStatus.VERIFYING
    incident.current_step = "verify_settle"
    recorder.save(incident)

    try:
        delay = (
            float(os.environ.get("VERIFY_POST_EXECUTION_SETTLE_SECONDS", "12"))
            if settle_seconds is None
            else float(settle_seconds)
        )
        if delay < 0:
            raise ValueError("VERIFY_POST_EXECUTION_SETTLE_SECONDS must be non-negative")
        if incident.execution is not None and incident.execution.success and delay:
            settle_sleep(delay)
    except Exception as exc:
        result = _result(
            incident,
            verdict=VerificationVerdict.CANNOT_VERIFY,
            telemetry_ok=False,
            video_ok=False,
            health_value=None,
            elapsed=0.0,
            samples=[],
            failed_checks=[f"settle delay error: {type(exc).__name__}: {exc}"],
        )
        incident.verification = result
        recorder.save(incident)
        log_event("verify", incident_id, "verify", "cannot_verify",
                  action=result.action, detail="; ".join(result.failed_checks))
        return result

    incident.current_step = "verify"
    recorder.save(incident)
    result = verify_recovery(incident)
    incident.verification = result
    incident.status = IncidentStatus.VERIFYING
    incident.current_step = "verify"
    recorder.save(incident)
    log_event("verify", incident_id, "verify", result.verdict.value.lower(),
              action=result.action, detail="; ".join(result.failed_checks) or "both domains healthy")
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 verify_recovery.py <incident_id>")
    try:
        verification = run_for_incident(sys.argv[1])
    except KeyError:
        sys.exit(f"ERROR: no incident {sys.argv[1]!r} in Firestore")
    except Exception as exc:
        sys.exit(f"ERROR: could not persist verification: {exc}")
    print(verification.model_dump_json(indent=2))
    if verification.verdict is not VerificationVerdict.RECOVERED:
        sys.exit(1)
