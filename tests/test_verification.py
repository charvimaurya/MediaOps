import pytest

from agent.verification import Verifier

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


class FakePrometheusClient:
    """No network -- returns canned telemetry, or raises if given an
    exception instance."""

    def __init__(self, telemetry_or_exception):
        self._telemetry_or_exception = telemetry_or_exception

    def get_telemetry(self):
        if isinstance(self._telemetry_or_exception, Exception):
            raise self._telemetry_or_exception
        return self._telemetry_or_exception


def test_healthy_telemetry_passes():
    verifier = Verifier(FakePrometheusClient(HEALTHY))
    result = verifier.verify(settle_seconds=0)  # no real sleeping
    assert result.passed is True
    assert result.failed_checks == []


@pytest.mark.parametrize(
    "override, expected_check",
    [
        ({"fps": 10.0}, "fps_below_floor"),
        ({"cpu_usage": 95.0}, "cpu_usage_too_high"),
        ({"encoding_latency": 200.0}, "encoding_latency_too_high"),
        ({"dropped_frames": 10.0}, "dropped_frames_too_high"),
        ({"encoder_status": 0}, "encoder_status_not_healthy"),
    ],
)
def test_each_unhealthy_condition_fails_and_names_itself(override, expected_check):
    telemetry = {**HEALTHY, **override}
    verifier = Verifier(FakePrometheusClient(telemetry))
    result = verifier.verify(settle_seconds=0)
    assert result.passed is False
    assert expected_check in result.failed_checks


def test_missing_metric_is_named_as_missing_not_silently_ignored():
    telemetry = dict(HEALTHY)
    del telemetry["encoder_status"]
    verifier = Verifier(FakePrometheusClient(telemetry))
    result = verifier.verify(settle_seconds=0)
    assert result.passed is False
    assert "encoder_status_missing" in result.failed_checks


def test_prometheus_error_is_treated_as_verification_failed_not_exception():
    verifier = Verifier(FakePrometheusClient(ConnectionError("simulated outage")))
    result = verifier.verify(settle_seconds=0)  # must not raise
    assert result.passed is False
    assert "prometheus_unreachable" in result.failed_checks
