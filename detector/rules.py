"""
Pure rule functions: a telemetry dict in, an IncidentType or None out.
No state, no side effects, no I/O. Every threshold lives in THRESHOLDS so
it can be tuned for the demo without touching the logic below.
"""

from typing import Optional

from detector.models import IncidentType

THRESHOLDS = {
    "encoder_status_failed": 0,
    "fps_zero": 0,
    "bitrate_zero": 0,
    "packet_loss_pct": 2.0,
    "network_latency_ms": 150,
    "cpu_usage_pct": 90,
    "encoding_latency_ms": 150,
    "fps_floor": 24,
    "dropped_frames_pct": 5,
}


def evaluate(telemetry: dict) -> Optional[IncidentType]:
    """Evaluate rules in priority order, returning on the first match, so
    a dead encoder is never misclassified as an overload. Metrics absent
    from `telemetry` (rather than present as 0) simply skip the rules
    that depend on them, per detector/prometheus.py's contract."""

    encoder_status = telemetry.get("encoder_status")
    fps = telemetry.get("fps")
    bitrate = telemetry.get("bitrate")
    packet_loss = telemetry.get("packet_loss")
    network_latency = telemetry.get("network_latency")
    cpu_usage = telemetry.get("cpu_usage")
    encoding_latency = telemetry.get("encoding_latency")

    # 1. encoder_failure
    if encoder_status is not None and encoder_status == THRESHOLDS["encoder_status_failed"]:
        return IncidentType.ENCODER_FAILURE
    if (
        fps is not None
        and bitrate is not None
        and fps == THRESHOLDS["fps_zero"]
        and bitrate == THRESHOLDS["bitrate_zero"]
    ):
        return IncidentType.ENCODER_FAILURE

    # 2. network_degradation
    if packet_loss is not None and packet_loss > THRESHOLDS["packet_loss_pct"]:
        return IncidentType.NETWORK_DEGRADATION
    if network_latency is not None and network_latency > THRESHOLDS["network_latency_ms"]:
        return IncidentType.NETWORK_DEGRADATION

    # 3. encoder_overload
    if cpu_usage is not None and cpu_usage > THRESHOLDS["cpu_usage_pct"]:
        latency_bad = encoding_latency is not None and encoding_latency > THRESHOLDS["encoding_latency_ms"]
        fps_bad = fps is not None and fps < THRESHOLDS["fps_floor"]
        if latency_bad or fps_bad:
            return IncidentType.ENCODER_OVERLOAD

    return None


def is_healthy(telemetry: dict) -> bool:
    fps = telemetry.get("fps")
    cpu_usage = telemetry.get("cpu_usage")
    encoding_latency = telemetry.get("encoding_latency")
    dropped_frames = telemetry.get("dropped_frames")
    encoder_status = telemetry.get("encoder_status")

    if None in (fps, cpu_usage, encoding_latency, dropped_frames, encoder_status):
        return False

    return (
        fps >= THRESHOLDS["fps_floor"]
        and cpu_usage < THRESHOLDS["cpu_usage_pct"]
        and encoding_latency < THRESHOLDS["encoding_latency_ms"]
        and dropped_frames < THRESHOLDS["dropped_frames_pct"]
        and encoder_status == 1
    )
