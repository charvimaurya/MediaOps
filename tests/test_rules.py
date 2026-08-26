import pytest

from detector import rules
from detector.models import IncidentType

HEALTHY = {
    "fps": 30.0,
    "cpu_usage": 45.0,
    "memory_usage": 50.0,
    "bitrate": 5.2,
    "encoding_latency": 42.0,
    "dropped_frames": 0.2,
    "packet_loss": 0.1,
    "network_latency": 30.0,
    "encoder_status": 1,
}


def _with(**overrides):
    telemetry = dict(HEALTHY)
    telemetry.update(overrides)
    return telemetry


@pytest.mark.parametrize(
    "telemetry, expected",
    [
        (HEALTHY, None),
        (_with(encoder_status=0), IncidentType.ENCODER_FAILURE),
        (_with(fps=0, bitrate=0), IncidentType.ENCODER_FAILURE),
        (_with(packet_loss=5), IncidentType.NETWORK_DEGRADATION),
        (_with(network_latency=200), IncidentType.NETWORK_DEGRADATION),
        (_with(cpu_usage=97, encoding_latency=190, fps=18), IncidentType.ENCODER_OVERLOAD),
        (_with(cpu_usage=97, encoding_latency=190), IncidentType.ENCODER_OVERLOAD),
        (_with(cpu_usage=97, fps=18), IncidentType.ENCODER_OVERLOAD),
        # cpu alone, without a second overload symptom, doesn't qualify
        (_with(cpu_usage=97), None),
    ],
)
def test_rule_classification(telemetry, expected):
    assert rules.evaluate(telemetry) == expected


def test_ordering_encoder_failure_beats_overload():
    telemetry = _with(cpu_usage=97, encoding_latency=190, fps=18, encoder_status=0)
    assert rules.evaluate(telemetry) == IncidentType.ENCODER_FAILURE


def test_ordering_encoder_failure_beats_network_degradation():
    telemetry = _with(packet_loss=10, network_latency=500, encoder_status=0)
    assert rules.evaluate(telemetry) == IncidentType.ENCODER_FAILURE


@pytest.mark.parametrize(
    "telemetry, expected",
    [
        (_with(packet_loss=2.0), None),  # exactly on threshold -- not > 2.0
        (_with(packet_loss=2.01), IncidentType.NETWORK_DEGRADATION),
        (_with(network_latency=150), None),  # exactly on threshold -- not > 150
        (_with(network_latency=150.01), IncidentType.NETWORK_DEGRADATION),
        (_with(cpu_usage=90, fps=18), None),  # exactly on threshold -- not > 90
        (_with(cpu_usage=90.01, fps=18), IncidentType.ENCODER_OVERLOAD),
        (_with(cpu_usage=97, fps=24), None),  # fps exactly at floor -- not < 24
        (_with(cpu_usage=97, encoding_latency=150), None),  # exactly on threshold -- not > 150
        (_with(cpu_usage=97, encoding_latency=150.01), IncidentType.ENCODER_OVERLOAD),
    ],
)
def test_boundary_values(telemetry, expected):
    assert rules.evaluate(telemetry) == expected


def test_missing_metrics_skip_dependent_rules_rather_than_treated_as_zero():
    # cpu_usage missing entirely -- overload rule must not fire just
    # because fps/encoding_latency look bad.
    telemetry = {"encoding_latency": 300, "fps": 10}
    assert rules.evaluate(telemetry) is None


def test_missing_encoder_status_does_not_fire_encoder_failure_as_zero():
    telemetry = _with()
    del telemetry["encoder_status"]
    assert rules.evaluate(telemetry) is None


def test_is_healthy_true_for_healthy_telemetry():
    assert rules.is_healthy(HEALTHY) is True


def test_is_healthy_false_when_any_field_missing():
    telemetry = dict(HEALTHY)
    del telemetry["dropped_frames"]
    assert rules.is_healthy(telemetry) is False


def test_is_healthy_false_when_overload_symptom_present():
    assert rules.is_healthy(_with(cpu_usage=95)) is False
